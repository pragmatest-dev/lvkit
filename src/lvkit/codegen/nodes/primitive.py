"""Code generator for LabVIEW primitives."""

from __future__ import annotations

import ast
import re

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind, Terminal
from lvkit.parser.constants import OPERATION_NODE_CLASSES
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
    uint_mask,
)
from ..context import CodeGenContext
from ..elementwise import LV_IMPORT, arrayify
from ..fragment import CodeFragment
from ..unresolved import emit_soft_unresolved


def _has_array_input(node: PrimitiveNode) -> bool:
    """True if any wired input terminal carries an array type."""
    return any(
        t.direction == "input"
        and t.lv_type is not None
        and t.lv_type.kind == LVTypeKind.ARRAY
        for t in node.terminals
    )


def _paren_if_compound(expr: str) -> str:
    """Wrap an inlined operand in parens if it is a compound expression.

    Operand substitution into a template is string-level, so an inlined
    sub-expression must keep its precedence: an upstream ``z | x`` dropped into
    ``in_1 & in_2`` has to stay ``(z | x) & …``, not collapse to ``z | x & …``
    (``&`` binds tighter than ``|``). Wrapping is always safe — redundant parens
    never change meaning — and also fixes ``~in_1`` / ``in_1[0]`` / etc."""
    try:
        node = ast.parse(expr, mode="eval").body
    except (SyntaxError, ValueError):
        return expr
    if isinstance(
        node,
        (
            ast.BoolOp,
            ast.BinOp,
            ast.UnaryOp,
            ast.Compare,
            ast.IfExp,
            ast.Lambda,
            ast.NamedExpr,
            ast.Await,
        ),
    ):
        return f"({expr})"
    return expr


def _int_input_terminal(node: PrimitiveNode) -> Terminal | None:
    """First input terminal carrying a LabVIEW integer type, else None.

    LabVIEW's boolean-logic prims (And/Or/Not) are BITWISE on integer operands;
    this drives that dispatch (mirrors _has_array_input for the array case)."""
    for t in node.terminals:
        if t.direction != "input" or t.lv_type is None:
            continue
        ut = t.lv_type.underlying_type or ""
        if ut.startswith("NumInt") or ut.startswith("NumUInt"):
            return t
    return None


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a primitive node."""
    prim_id = node.prim_id

    # Merge Errors (prim 2147; not 2401, which is Swap Values -- #59) is a
    # structural signal, not a code node. In the exception model, error
    # merging happens via try/except on future.result() calls — the
    # primitive itself produces no code.
    if prim_id == 2147:
        return CodeFragment.empty()

    # Get primitive hint.
    # Specialized node_types (subset, aBuild, etc.) take priority over
    # prim_id because some primResIDs are shared between different
    # functions (e.g., 1516 = both Array Subset and Select, distinguished
    # by XML class). Generic "prim" nodes use prim_id lookup.
    # If node_type resolves but has no usable code, fall through to prim_id.
    resolver = get_resolver()
    resolved = None
    node_type = getattr(node, "node_type", None)
    if node_type and node_type != "prim":
        resolved = resolver.resolve_by_node_type(node_type)
        # Fall through if node_type resolved but has no code
        if resolved and not resolved.python_code:
            resolved = None
    if not resolved and prim_id is not None:
        resolved = resolver.resolve(prim_id=prim_id)
    if not resolved:
        # A node class captured GENERICALLY (not in the handled allowlist —
        # e.g. decimate, interLeave, extFunc, exprNode) reaches here with no
        # primResID. Fail loudly via _emit_unknown rather than silently drop it
        # to empty code. Known-but-unimplemented classes (concat, …) keep prior
        # behavior — their missing handler is a separate, pre-existing gap.
        is_generic_unknown = bool(
            prim_id is None
            and node_type
            and node_type != "prim"
            and node_type not in OPERATION_NODE_CLASSES
        )
        if prim_id is None and not is_generic_unknown:
            return CodeFragment.empty()
        return _emit_unknown(node, prim_id or 0, ctx)

    # Placeholder: emit warning comment + pass, don't raise
    if resolved.confidence == "placeholder":
        return _emit_placeholder(node, resolved, ctx)

    # LabVIEW And/Or/Not are BITWISE on integer operands. When an input carries
    # an integer type, prefer the primitive's integer template; Not (1064) needs
    # a width-masked complement no static template can express, so build it here.
    int_in = _int_input_terminal(node)
    if int_in is not None:
        if prim_id == 1064:  # Not -> ~x, masked to width for UNSIGNED ints
            m = uint_mask(int_in.lv_type)
            expr = f"(~in_1) & 0x{m:X}" if m is not None else "~in_1"
            if isinstance(resolved.python_code, dict) and resolved.python_code:
                key = next(iter(resolved.python_code))
                resolved.python_code = {key: expr}
            else:
                resolved.python_code = expr
        elif resolved.python_code_int is not None:
            resolved.python_code = resolved.python_code_int

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
            node,
            resolved.python_code,
            input_map,
            ctx,
            resolved,
        )

    # Get wired output terminals (excluding passthroughs)
    wired_outputs = _get_wired_outputs(
        node,
        resolved,
        ctx,
        skip_term_ids=passthrough_term_ids,
    )

    # Numeric primitives are element-wise over arrays. When this op is flagged
    # elementwise and an operand is an array, broadcast its operators.
    arrayify_ops = bool(resolved.elementwise and _has_array_input(node))

    # Build code based on code type
    if isinstance(resolved.python_code, dict):
        fragment = _build_dict_hint(
            resolved.python_code,
            input_map,
            wired_outputs,
            ctx,
            resolved,
            arrayify_ops,
        )
    else:
        fragment = _build_string_hint(
            resolved.python_code or "",
            input_map,
            wired_outputs,
            ctx,
            resolved,
            arrayify_ops,
        )

    # Merge passthrough bindings
    fragment.bindings.update(passthrough_bindings)

    # Record array-typed output variables so a final pass can broadcast
    # operators over them even after single-use expression inlining.
    for term in node.terminals:
        if (
            term.direction == "output"
            and term.lv_type is not None
            and term.lv_type.kind == LVTypeKind.ARRAY
        ):
            bound = fragment.bindings.get(term.id)
            if bound and bound.isidentifier():
                ctx.array_vars.add(bound)

    # Add imports from primitive definition (normalize bare module names)
    if resolved.imports:
        for imp in resolved.imports:
            if not imp.startswith(("import ", "from ")):
                fragment.imports.add(f"import {imp}")
            else:
                fragment.imports.add(imp)

    return fragment


def _expandable_base(term_index: int, expandable_indices: set[int]) -> int | None:
    """Return the expandable-group base index ``term_index`` belongs to, else None.

    Expanded terminals sit at a stride from their base (e.g. base=2 for an index
    expanded to 2D gives indices 2, 4), so a terminal belongs to a group when it
    IS the base or lands an exact multiple of the stride beyond it.
    """
    for base in expandable_indices:
        if term_index == base or (
            term_index > base
            and (term_index - base) % max(len(expandable_indices), 1) == 0
        ):
            return base
    return None


def _build_input_map(
    node: PrimitiveNode, ctx: CodeGenContext, resolved: ResolvedPrimitive | None
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
        # An error terminal belonging to an EXPANDABLE group is also data to
        # this primitive (Merge Errors folds N error clusters), but the template
        # reaches it via {expandable_inputs} rather than a literal in_N — so it
        # must survive the skip or every extra error input is silently dropped.
        if term.is_error_cluster:
            if (
                str(term.index) not in template_refs
                and _expandable_base(term.index, expandable_indices) is None
            ):
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
                    node,
                    term,
                    resolved,
                    ctx,
                )
            resolved_value = value
        elif default_value is not None:
            resolved_value = default_value
        else:
            # Unwired terminal — use default from JSON or type-based default
            ut = (term.lv_type.underlying_type or "") if term.lv_type else ""
            if ut == "Refnum" and node.prim_id in (8010, 8011, 8003, 8005):
                vi_short = (
                    (ctx.vi_name or "output")
                    .replace(".vi", "")
                    .replace(":", "_")
                    .replace(".", "_")
                )
                resolved_value = f"open(Path(__file__).parent / '{vi_short}.txt', 'a+')"
                ctx.imports.add("from pathlib import Path")
            elif ut == "Path" and node.prim_id in (9101,):
                resolved_value = "Path(__file__)"
                ctx.imports.add("from pathlib import Path")
            else:
                # Default for unwired terminal — use the type
                resolved_value = _default_for_type(term, ctx)

        # Expandable terminal: collect into group by base index.
        # Expanded terminals have indices that are offset from the base
        # (e.g., base=2 for index, expanded 2D gives indices 2, 4).
        base_idx = _expandable_base(term_index, expandable_indices)
        matched_expandable = base_idx is not None
        if base_idx is not None:
            expandable_groups.setdefault(base_idx, []).append(resolved_value)

        if not matched_expandable:
            # Parenthesize a compound operand so its precedence survives the
            # string-level substitution into an operator template.
            operand = _paren_if_compound(resolved_value)
            # Add index-based key so templates can use in_1, in_2 etc.
            input_map[f"in_{term_index}"] = operand
            if term_name:
                input_map[term_name] = operand
                input_map[to_var_name(term_name)] = operand

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

    # Fill defaults for template terminals the heap did not serialize at all
    # (truly-absent optional terminals). NOTE: merely UNWIRED terminals DO
    # appear in node.terminals — the heap serializes the full connector pane —
    # so this only covers indices a template references that aren't in the
    # node's termList. Use the JSON default or None.
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
    node: PrimitiveNode,
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
        if re.match(r"^in_\d+$", expr_template):
            resolved_var = input_map.get(expr_template)
            if (
                resolved_var
                and resolved_var.isidentifier()
                and resolved_var not in ("None", "True", "False")
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
    node: PrimitiveNode,
    resolved: ResolvedPrimitive | None,
    ctx: CodeGenContext,
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
                node,
                term,
                resolved,
                ctx,
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
    arrayify_ops: bool = False,
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
            expr_substituted = _substitute_template(exprs[i], input_map, resolved)
            expr_ast = parse_expr(expr_substituted)
            if arrayify_ops:
                expr_ast, used = arrayify(expr_ast)
                if used:
                    imports.add(LV_IMPORT)
            statements.append(build_assign(var_name, expr_ast))
            bindings[term_id] = var_name
        else:
            # More outputs than expressions — placeholder
            statements.append(build_assign(var_name, ast.Constant(value=None)))
            bindings[term_id] = var_name

    return CodeFragment(statements=statements, bindings=bindings, imports=imports)


def _build_string_hint(
    hint: str,
    input_map: dict[str, str],
    wired_outputs: list[tuple[str, str, str]],
    ctx: CodeGenContext,
    resolved: ResolvedPrimitive | None,
    arrayify_ops: bool = False,
) -> CodeFragment:
    """Build code from string-format hint."""
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    imports: set[str] = set()

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
    if arrayify_ops:
        expr_ast, used = arrayify(expr_ast)
        if used:
            imports.add(LV_IMPORT)

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

    return CodeFragment(statements=statements, bindings=bindings, imports=imports)


def _substitute_template(
    template: str,
    input_map: dict[str, str],
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
    result = re.sub(r"\bin_(\d+)\b", "None", result)

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
            "int",
            "float",
            "numeric",
        ):
            return "0"
        if lv_type.kind == LVTypeKind.ARRAY:
            return "[]"
    return "None"


def _terminal_signature(
    node: PrimitiveNode,
    ctx: CodeGenContext,
) -> list[dict[str, str | int | bool | None]]:
    """The FULL connector pane (every terminal, wired AND unwired) as the
    resolution diagnostics carry it — the identity a resolver matches against."""
    return [
        {
            "index": term.index,
            "direction": term.direction,
            "name": term.name,
            "type": term.lv_type.underlying_type if term.lv_type else None,
            "wired": ctx.is_wired(term.id),
        }
        for term in node.terminals
    ]


def _emit_placeholder(
    node: PrimitiveNode,
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

    if ctx.unresolved_sink is not None:
        # A placeholder is a KNOWN primitive with no implementation — still a
        # conversion gap. Record it (tagged) so `lvkit unresolved` reports it
        # distinctly from a fully-unknown primResID.
        exc = PrimitiveResolutionNeeded(
            prim_id=prim_id,
            prim_name=name,
            terminals=_terminal_signature(node, ctx),
            vi_name=ctx.vi_name,
            qualified_vi_name=ctx.qualified_vi_name,
        )
        exc.is_placeholder = True  # type: ignore[attr-defined]
        ctx.unresolved_sink.append(exc)

    # String literal acts as inline documentation in generated code
    marker = ast.Expr(
        value=ast.Constant(value=f"TODO: unresolved primitive {prim_id} ({name})")
    )
    return CodeFragment(statements=[marker, ast.Pass()])


def _emit_unknown(
    node: PrimitiveNode, prim_id: int, ctx: CodeGenContext
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
    # The FULL connector pane — node.terminals carries every terminal the heap
    # serialized, wired AND unwired, each with its declared type. Mark wired
    # status so the resolver can identify by the whole pane, not just the
    # terminals that happen to have wires in this VI.
    terminals = _terminal_signature(node, ctx)

    kwargs: dict[str, object] = {
        "prim_id": prim_id,
        "prim_name": node.name or "unknown",
        "terminals": terminals,
        "vi_name": ctx.vi_name,
        "qualified_vi_name": ctx.qualified_vi_name,
    }

    if ctx.unresolved_sink is not None:
        # Batch-collection mode (`lvkit unresolved`): record the gap and keep
        # generating so every gap in the VI is surfaced in one pass.
        ctx.unresolved_sink.append(
            PrimitiveResolutionNeeded(**kwargs)  # type: ignore[arg-type]
        )

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
    node: PrimitiveNode,
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
    assigned_indices = {t.index for t in node.terminals if t.index >= 0}
    avail = [
        {"index": rt.index, "name": rt.name, "type": rt.type}
        for rt in (resolved.terminals if resolved else [])
        if rt.direction == direction and rt.index not in assigned_indices
    ]
    raise TerminalResolutionNeeded(
        prim_id=node.prim_id or 0,
        prim_name=node.name or "unknown",
        terminal_direction=term.direction,
        terminal_type=(term.lv_type.underlying_type if term.lv_type else None),
        available=avail,
        vi_name=ctx.vi_name,
    )
