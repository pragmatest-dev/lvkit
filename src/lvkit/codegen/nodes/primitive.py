"""Code generator for LabVIEW primitives."""

from __future__ import annotations

import ast
import re

from lvkit.models import PrimitiveOperation, Terminal
from lvkit.primitive_resolver import (
    PrimitiveResolutionNeeded,
    ResolvedPrimitive,
    TerminalResolutionNeeded,
    get_resolver,
)

from ..ast_utils import (
    build_assign,
    parse_expr,
    parse_stmt,
    to_var_name,
)
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..unresolved import emit_soft_unresolved


def generate(node: PrimitiveOperation, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a primitive node."""
    prim_id = node.primResID

    # Merge Errors (prim 2401) is a structural signal, not a code node.
    # In the exception model, error merging happens via try/except on
    # future.result() calls — the primitive itself produces no code.
    if prim_id == 2401:
        return CodeFragment.empty()

    # Get primitive hint.
    # Specialized node_types (subset, aBuild, etc.) take priority over
    # prim_id because some primResIDs are shared between different
    # functions (e.g., 1516 = both Array Subset and Select, distinguished
    # by XML class). Generic "prim" nodes use prim_id lookup.
    # If node_type resolves but has no usable code, fall through to prim_id.
    resolver = get_resolver()
    resolved = None
    node_type = getattr(node, 'node_type', None)
    if node_type and node_type != 'prim':
        resolved = resolver.resolve_by_node_type(node_type)
        # Fall through if node_type resolved but has no code
        if resolved and not resolved.python_code:
            resolved = None
    if not resolved and prim_id is not None:
        resolved = resolver.resolve(prim_id=prim_id)
    if not resolved:
        if prim_id is None:
            return CodeFragment.empty()
        return _emit_unknown(node, prim_id, ctx)

    # Placeholder: emit warning comment + pass, don't raise
    if resolved.confidence == "placeholder":
        return _emit_placeholder(node, resolved, ctx)

    # Check if primitive is truly unknown (no code, unknown confidence, or comment)
    code = resolved.python_code if resolved else None
    is_unknown = (
        not resolved
        or not code
        or resolved.confidence == "unknown"
        or (isinstance(code, str) and code.strip().startswith("#"))
    )
    if is_unknown:
        # Unknown primitive - emit explicit error
        return _emit_unknown(node, prim_id or 0, ctx)

    # Resolve input values from context (use resolved terminals for names)
    input_map = _build_input_map(node, ctx, resolved)

    # Detect passthrough outputs BEFORE allocating variable names.
    # Passthroughs (template is just `in_N`) bind directly to the input
    # variable — no assignment, no make_output_var() allocation.
    passthrough_bindings: dict[str, str] = {}
    passthrough_term_ids: set[str] = set()
    if isinstance(resolved.python_code, dict):
        passthrough_bindings, passthrough_term_ids = _detect_passthroughs(
            node, resolved.python_code, input_map, ctx, resolved,
        )

    # Get wired output terminals (excluding passthroughs)
    wired_outputs = _get_wired_outputs(
        node, resolved, ctx, skip_term_ids=passthrough_term_ids,
    )

    # Build code based on code type
    if isinstance(resolved.python_code, dict):
        fragment = _build_dict_hint(
            resolved.python_code, input_map, wired_outputs, ctx, resolved
        )
    else:
        fragment = _build_string_hint(
            resolved.python_code or "", input_map, wired_outputs, ctx, resolved
        )

    # Merge passthrough bindings
    fragment.bindings.update(passthrough_bindings)

    # Add imports from primitive definition (normalize bare module names)
    if resolved.imports:
        for imp in resolved.imports:
            if not imp.startswith(("import ", "from ")):
                fragment.imports.add(f"import {imp}")
            else:
                fragment.imports.add(imp)

    return fragment

def _build_input_map(
    node: PrimitiveOperation, ctx: CodeGenContext, resolved: ResolvedPrimitive | None
) -> dict[str, str]:
    """Build mapping from terminal names to resolved variable names.

    Uses primitive resolver terminal names when node terminals lack names.
    Matches by connector pane index (sparse — not sequential).
    When a terminal is unwired, uses the default_value from the primitive
    definition if available, otherwise "None".
    """
    input_map = {}
    # Expandable groups: base_index → list of resolved values (in dimension order)
    expandable_groups: dict[int, list[str]] = {}

    # Build index → (name, default_value) dict from resolved terminals
    resolved_inputs: dict[int, tuple[str, str | None]] = {}
    expandable_indices: set[int] = set()
    if resolved and resolved.terminals:
        for rt in resolved.terminals:
            if rt.direction == "in":
                default = getattr(rt, "default_value", None)
                resolved_inputs[rt.index] = (rt.name or "", default)
                if getattr(rt, "expandable", False):
                    expandable_indices.add(rt.index)

    # Check which terminal indices the template actually references
    template_str = str(resolved.python_code) if resolved else ""
    template_refs = set(re.findall(r"\bin_(\d+)\b", template_str))

    for term in node.terminals:
        if term.direction != "input":
            continue

        # Skip error cluster inputs unless the template references
        # them (e.g. Merge Errors processes error data as values).
        if term.is_error_cluster:
            if str(term.index) not in template_refs:
                continue

        term_id = term.id
        term_index = term.index
        term_name = term.name or ""
        default_value = None

        # Match by connector pane index (sparse dict lookup)
        if term_index in resolved_inputs:
            resolved_name, default_value = resolved_inputs[term_index]
            if not term_name:
                term_name = resolved_name

        # Resolve from context - None means unwired
        value = ctx.resolve(term_id)
        if value:
            # Wired terminal with -1 index: resolution failure
            if term_index == -1:
                _raise_terminal_resolution(
                    node, term, resolved, ctx,
                )
            resolved_value = value
        elif default_value is not None:
            resolved_value = default_value
        else:
            # Unwired terminal — use default from JSON or type-based default
            ut = (term.lv_type.underlying_type or "") if term.lv_type else ""
            if (
                ut == "Refnum"
                and node.primResID in (8010, 8011, 8003, 8005)
            ):
                vi_short = (
                    (ctx.vi_name or "output")
                    .replace(".vi", "")
                    .replace(":", "_")
                    .replace(".", "_")
                )
                resolved_value = (
                    f"open(Path(__file__).parent / '{vi_short}.txt', 'a+')"
                )
                ctx.imports.add("from pathlib import Path")
            elif (
                ut == "Path"
                and node.primResID in (9101,)
            ):
                resolved_value = "Path(__file__)"
                ctx.imports.add("from pathlib import Path")
            else:
                # Default for unwired terminal — use the type
                resolved_value = _default_for_type(term, ctx)

        # Expandable terminal: collect into group by base index.
        # Expanded terminals have indices that are offset from the base
        # (e.g., base=2 for index, expanded 2D gives indices 2, 4).
        matched_expandable = False
        if expandable_indices:
            for base_idx in expandable_indices:
                if term_index == base_idx or (
                    term_index > base_idx
                    and (term_index - base_idx)
                    % max(len(expandable_indices), 1) == 0
                ):
                    if base_idx not in expandable_groups:
                        expandable_groups[base_idx] = []
                    expandable_groups[base_idx].append(resolved_value)
                    matched_expandable = True
                    break

        if not matched_expandable:
            # Add index-based key so templates can use in_1, in_2 etc.
            input_map[f"in_{term_index}"] = resolved_value
            if term_name:
                input_map[term_name] = resolved_value
                input_map[to_var_name(term_name)] = resolved_value

    # Add expandable placeholders for template substitution.
    # Single group: {expandable_inputs} for backward compat.
    # Multiple groups: {name_values} per group (e.g., {index_values}).
    if len(expandable_groups) == 1:
        values = list(expandable_groups.values())[0]
        input_map["expandable_inputs"] = ", ".join(values)
    elif expandable_groups:
        for base_idx, values in expandable_groups.items():
            name = resolved_inputs.get(base_idx, ("expandable",))[0]
            key = to_var_name(name) + "_values"
            input_map[key] = ", ".join(values)

    # Fill defaults for JSON-defined terminals not in the node.
    # Unwired terminals don't appear in node.terminals but templates
    # may reference them as in_N. Use the JSON default or None.
    if resolved and resolved.terminals:
        node_indices = {t.index for t in node.terminals}
        for rt in resolved.terminals:
            if rt.direction == "in" and rt.index not in node_indices:
                key = f"in_{rt.index}"
                if key not in input_map:
                    default = getattr(rt, "default_value", None)
                    input_map[key] = default if default is not None else "None"

    return input_map

def _detect_passthroughs(
    node: PrimitiveOperation,
    hint: dict[str, str],
    input_map: dict[str, str],
    ctx: CodeGenContext,
    resolved: ResolvedPrimitive | None,
) -> tuple[dict[str, str], set[str]]:
    """Detect output terminals that are pure passthroughs.

    A passthrough is when the template expression is just `in_N` — the
    output IS the input. For these, bind the output terminal directly to
    the input variable instead of allocating a new name.

    Uses the same output terminal iteration order as _build_dict_hint
    (skip error clusters, skip unwired) to match expressions by position.
    """
    bindings: dict[str, str] = {}
    skip_ids: set[str] = set()

    exprs = [(k, v) for k, v in hint.items() if k not in ("_body", "_import")]

    # Build resolved output name lookup
    resolved_outputs: dict[int, str] = {}
    if resolved and resolved.terminals:
        for rt in resolved.terminals:
            if rt.direction == "out":
                resolved_outputs[rt.index] = rt.name or ""

    # Iterate output terminals in the same order as _get_wired_outputs
    expr_idx = 0
    for term in node.terminals:
        if term.direction != "output":
            continue
        if term.is_error_cluster:
            continue
        if not ctx.is_wired(term.id):
            continue
        term_name = term.name or ""
        if not term_name and term.index in resolved_outputs:
            term_name = resolved_outputs[term.index]
        if expr_idx >= len(exprs):
            break
        _key, expr_template = exprs[expr_idx]
        expr_idx += 1

        # Case 1: bare input reference (in_N) — identity passthrough
        if re.match(r'^in_\d+$', expr_template):
            resolved_var = input_map.get(expr_template)
            if (
                resolved_var
                and resolved_var.isidentifier()
                and resolved_var not in ('None', 'True', 'False')
            ):
                bindings[term.id] = resolved_var
                skip_ids.add(term.id)
            continue

        # Case 2: single-use simple expression — inline into consumer
        # If this output has exactly one consumer and the expression is
        # simple (no function calls, no string literals), bind the
        # substituted expression directly. Turns
        # `equal_478 = x == y; if equal_478:` into `if x == y:`.
        # Skip if hint has _body — the _body creates variables that
        # output expressions depend on (e.g., Match Pattern's _m).
        if "_body" in hint:
            continue
        if ctx.graph is None:
            continue
        consumers = ctx.graph.outgoing_edges(term.id)
        if len(consumers) != 1:
            continue
        substituted = _substitute_template(expr_template, input_map, resolved)
        # Only inline simple expressions — no parens (function calls),
        # no quotes (string literals), no brackets (subscripts)
        if any(c in substituted for c in "('\"["):
            continue
        bindings[term.id] = substituted
        skip_ids.add(term.id)

    return bindings, skip_ids

def _get_wired_outputs(
    node: PrimitiveOperation, resolved: ResolvedPrimitive | None, ctx: CodeGenContext,
    skip_term_ids: set[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Get list of (terminal_id, terminal_name, var_name) for wired outputs.

    Matches by connector pane index (sparse dict lookup).
    Terminal names in the primitive JSON should be valid Python identifiers.
    """
    # Build index → name dict from resolved terminals
    resolved_outputs: dict[int, str] = {}
    expandable_out_index: int | None = None
    if resolved and resolved.terminals:
        for rt in resolved.terminals:
            if rt.direction == "out":
                resolved_outputs[rt.index] = rt.name or ""
                if getattr(rt, "expandable", False):
                    expandable_out_index = rt.index

    outputs = []
    for term in node.terminals:
        if term.direction != "output":
            continue

        # Skip passthrough terminals (already bound by _detect_passthroughs)
        if skip_term_ids and term.id in skip_term_ids:
            continue

        # Skip error terminals — detected by actual type, not JSON labels.
        # Polymorphic prims reuse IDs with different semantics, so JSON
        # error labels can be wrong (e.g., 8003 is Variant To Data, not
        # Open/Create/Replace File).
        if term.is_error_cluster:
            continue

        # Skip unwired outputs — no consumer, no assignment needed
        if not ctx.is_wired(term.id):
            continue

        term_id = term.id
        term_index = term.index
        term_name = term.name or ""

        # Match by connector pane index (sparse dict lookup)
        if not term_name and term_index in resolved_outputs:
            term_name = resolved_outputs[term_index]

        # Expandable output: accept all terminals mapped to expandable index
        if expandable_out_index is not None and term_index == expandable_out_index:
            base_name = resolved_outputs.get(expandable_out_index, "element")
            var_name = to_var_name(base_name) + f"_{len(outputs)}"
            outputs.append((term_id, term_name or base_name, var_name))
            continue

        # Output with -1 index and no name: resolution failure
        if term_index == -1 and not term_name:
            _raise_terminal_resolution(
                node, term, resolved, ctx,
            )

        var_name = (
            ctx.make_output_var(term_name, node.id, terminal_id=term_id)
            if term_name
            else f"out_{term_index}"
        )
        outputs.append((term_id, term_name, var_name))

    return outputs

def _build_dict_hint(
    hint: dict[str, str],
    input_map: dict[str, str],
    wired_outputs: list[tuple[str, str, str]],
    ctx: CodeGenContext,
    resolved: ResolvedPrimitive | None,
) -> CodeFragment:
    """Build code from dict-format hint.

    Dict format:
    - "_body": Optional statement to execute first
    - other keys: output_name → expression
    """
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    imports: set[str] = set()

    # Handle _import (add to fragment imports)
    imp = hint.get("_import")
    if imp:
        imports.add(imp)

    # Handle _body (side effect statement)
    body = hint.get("_body")
    if body:
        body_substituted = _substitute_template(body, input_map, resolved)
        statements.append(parse_stmt(body_substituted))

    # Handle each output — match by position, not name.
    # The graph knows the literal connections; we just need
    # to pair each wired output with its expression.
    exprs = [v for k, v in hint.items() if k not in ("_body", "_import")]
    for i, (term_id, term_name, var_name) in enumerate(wired_outputs):
        if i < len(exprs):
            expr_substituted = _substitute_template(
                exprs[i], input_map, resolved
            )
            expr_ast = parse_expr(expr_substituted)
            statements.append(build_assign(var_name, expr_ast))
            bindings[term_id] = var_name
        else:
            # More outputs than expressions — placeholder
            statements.append(
                build_assign(var_name, ast.Constant(value=None))
            )
            bindings[term_id] = var_name

    return CodeFragment(statements=statements, bindings=bindings, imports=imports)

def _build_string_hint(
    hint: str,
    input_map: dict[str, str],
    wired_outputs: list[tuple[str, str, str]],
    ctx: CodeGenContext,
    resolved: ResolvedPrimitive | None,
) -> CodeFragment:
    """Build code from string-format hint."""
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}

    # Strip assignment if present in hint
    expr = hint
    if "=" in expr and not any(op in expr for op in ["==", "!=", "<=", ">="]):
        eq_pos = expr.find("=")
        if eq_pos > 0 and expr[eq_pos - 1] not in "!<>" and expr[eq_pos + 1] != "=":
            expr = expr[eq_pos + 1 :].strip()

    # Strip trailing comment
    if "#" in expr:
        expr = expr[: expr.find("#")].strip()

    # Substitute inputs
    expr_substituted = _substitute_template(expr, input_map, resolved)

    expr_ast = parse_expr(expr_substituted)

    # Assign to output variables
    if len(wired_outputs) == 1:
        term_id, _, var_name = wired_outputs[0]
        statements.append(build_assign(var_name, expr_ast))
        bindings[term_id] = var_name
    elif len(wired_outputs) > 1:
        # Multiple outputs - unpack tuple
        var_names = [v for _, _, v in wired_outputs]
        statements.append(
            ast.Assign(
                targets=[
                    ast.Tuple(
                        elts=[ast.Name(id=v, ctx=ast.Store()) for v in var_names],
                        ctx=ast.Store(),
                    )
                ],
                value=expr_ast,
            )
        )
        for term_id, _, var_name in wired_outputs:
            bindings[term_id] = var_name
    else:
        # No outputs - just expression as statement
        statements.append(ast.Expr(value=expr_ast))

    return CodeFragment(statements=statements, bindings=bindings)

def _substitute_template(
    template: str, input_map: dict[str, str],
    resolved: ResolvedPrimitive | None = None,
) -> str:
    """Substitute variable names in template string.

    input_map contains terminal names and index-based keys (in_1, in_2)
    mapped to resolved variable names from the dataflow graph.

    Templates should use terminal names or index-based refs (in_1, in_2)
    to reference inputs by their actual wire connections.

    Uses single-pass replacement to avoid double-substitution when
    input names overlap with resolved values (e.g., x→y and y→x).
    """
    # Build a combined pattern matching all names (longest first).
    # Tries {name} placeholder first (consumes braces), then bare \bname\b.
    names = sorted(
        [n for n in input_map if n],
        key=lambda x: -len(x),
    )
    if not names:
        return template

    patterns = []
    for n in names:
        escaped = re.escape(n)
        patterns.append(r"\{" + escaped + r"\}")  # {name} with braces
        patterns.append(r"\b" + escaped + r"\b")  # bare name
    combined = "|".join(patterns)

    def _replace(m: re.Match) -> str:
        text = m.group()
        # Strip braces if matched as {name} placeholder
        key = text.strip("{}") if text.startswith("{") else text
        return input_map[key] if key in input_map else text

    result = re.sub(combined, _replace, template)

    # Replace any remaining unsubstituted in_N placeholders with
    # type default. This happens for unwired optional inputs — the
    # terminal exists in the primitive definition but has no wire.
    result = re.sub(r'\bin_(\d+)\b', 'None', result)

    return result

def _default_for_type(term: Terminal, ctx: CodeGenContext) -> str:
    """Return a Python default value based on the terminal's lv_type."""
    lv_type = term.lv_type
    if lv_type:
        ut = lv_type.underlying_type or ""
        if ut == "Boolean":
            return "False"
        if ut == "String":
            return "''"
        if ut == "Path":
            ctx.imports.add("from pathlib import Path")
            return "Path('.')"
        if ut.startswith("Num") or lv_type.kind in (
            "int", "float", "numeric",
        ):
            return "0"
        if lv_type.kind == "array":
            return "[]"
    return "None"

def _emit_placeholder(
    node: PrimitiveOperation,
    resolved: ResolvedPrimitive,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Emit a pass + warning for placeholder primitives.

    Allows generation to proceed while flagging unresolved primitives.
    """
    import warnings

    prim_id = resolved.prim_id or "?"
    name = resolved.name or "unknown"
    msg = f"Placeholder primitive {prim_id} ({name})"
    warnings.warn(msg, stacklevel=2)

    # String literal acts as inline documentation in generated code
    marker = ast.Expr(value=ast.Constant(
        value=f"TODO: unresolved primitive {prim_id} ({name})"
    ))
    return CodeFragment(statements=[marker, ast.Pass()])

def _emit_unknown(
    node: PrimitiveOperation, prim_id: int, ctx: CodeGenContext
) -> CodeFragment:
    """Handle an unknown primitive.

    Default mode: raise PrimitiveResolutionNeeded immediately so the
    conversion loop catches it and the user can resolve it before
    proceeding.

    Soft mode (ctx.soft_unresolved=True): emit an inline `raise
    PrimitiveResolutionNeeded(...)` AST statement with the same kwargs.
    The generated Python is syntactically valid; running it raises the
    exact same exception that hard mode would have raised at codegen
    time. This lets a downstream LLM see the diagnostic in context and
    either write a mapping into .lvkit/ or replace the raise with a
    contextual fix.
    """
    terminals = [
        {
            "index": term.index,
            "direction": term.direction,
            "name": term.name,
            "type": term.lv_type.underlying_type if term.lv_type else None,
        }
        for term in node.terminals
    ]

    kwargs: dict[str, object] = {
        "prim_id": prim_id,
        "prim_name": node.name or "unknown",
        "terminals": terminals,
        "vi_name": ctx.vi_name,
        "qualified_vi_name": ctx.qualified_vi_name,
    }

    if not ctx.soft_unresolved:
        raise PrimitiveResolutionNeeded(**kwargs)  # type: ignore[arg-type]

    return emit_soft_unresolved(
        node=node,
        ctx=ctx,
        exception_module="lvkit.primitive_resolver",
        exception_class="PrimitiveResolutionNeeded",
        literal_kwargs=kwargs,
    )

def _raise_terminal_resolution(
    node: PrimitiveOperation,
    term: Terminal,
    resolved: ResolvedPrimitive | None,
    ctx: CodeGenContext,
) -> None:
    """Raise TerminalResolutionNeeded for a specific unresolved terminal.

    The primitive definition exists but this terminal's index is -1.
    Direction and type come from the terminal itself — never fabricated.
    """
    # Filter available resolver terminals to same direction, unassigned
    direction = "in" if term.direction == "input" else "out"
    assigned_indices = {
        t.index for t in node.terminals if t.index >= 0
    }
    avail = [
        {"index": rt.index, "name": rt.name, "type": rt.type}
        for rt in (resolved.terminals if resolved else [])
        if rt.direction == direction
        and rt.index not in assigned_indices
    ]
    raise TerminalResolutionNeeded(
        prim_id=node.primResID or 0,
        prim_name=node.name or "unknown",
        terminal_direction=term.direction,
        terminal_type=(
            term.lv_type.underlying_type if term.lv_type else None
        ),
        available=avail,
        vi_name=ctx.vi_name,
    )
