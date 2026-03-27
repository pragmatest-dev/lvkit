"""Code generator for LabVIEW primitives."""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

from vipy.graph_types import Operation
from vipy.primitive_resolver import TerminalResolutionNeeded

from ..ast_utils import (
    build_assign,
    parse_expr,
    parse_stmt,
    to_var_name,
)
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class PrimitiveCodeGen(NodeCodeGen):
    """Generate code for LabVIEW primitive operations.

    Uses primitive hints from the resolver to generate Python equivalents.
    Handles both simple (string) and complex (dict) hints.
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code for a primitive node."""
        from ....primitive_resolver import get_resolver

        prim_id = node.primResID

        # Get primitive hint — try prim_id first, then node_type
        resolver = get_resolver()
        resolved = None
        if prim_id is not None:
            resolved = resolver.resolve(prim_id=prim_id)
        if not resolved and hasattr(node, 'node_type') and node.node_type:
            resolved = resolver.resolve_by_node_type(node.node_type)
        if not resolved:
            if prim_id is None:
                return CodeFragment.empty()
            return self._emit_unknown(node, prim_id, ctx)

        # Check if primitive is truly unknown (no code, unknown confidence, or comment-only code)
        code = resolved.python_code if resolved else None
        is_unknown = (
            not resolved
            or not code
            or resolved.confidence == "unknown"
            or (isinstance(code, str) and code.strip().startswith("#"))
        )
        if is_unknown:
            # Unknown primitive - emit explicit error
            return self._emit_unknown(node, prim_id, ctx)

        # Resolve input values from context (use resolved terminals for names)
        input_map = self._build_input_map(node, ctx, resolved)

        # Detect passthrough outputs BEFORE allocating variable names.
        # Passthroughs (template is just `in_N`) bind directly to the input
        # variable — no assignment, no make_output_var() allocation.
        passthrough_bindings: dict[str, str] = {}
        passthrough_term_ids: set[str] = set()
        if isinstance(resolved.python_code, dict):
            passthrough_bindings, passthrough_term_ids = self._detect_passthroughs(
                node, resolved.python_code, input_map, ctx, resolved,
            )

        # Get wired output terminals (excluding passthroughs)
        wired_outputs = self._get_wired_outputs(
            node, resolved, ctx, skip_term_ids=passthrough_term_ids,
        )

        # Build code based on code type
        if isinstance(resolved.python_code, dict):
            fragment = self._build_dict_hint(
                resolved.python_code, input_map, wired_outputs, ctx, resolved
            )
        else:
            fragment = self._build_string_hint(
                resolved.python_code, input_map, wired_outputs, ctx, resolved
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
        self, node: Operation, ctx: CodeGenContext, resolved: Any
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
        # Also track which resolver indices are error/expandable terminals
        resolver_error_indices: set[int] = set()
        expandable_indices: set[int] = set()
        if resolved and resolved.terminals:
            for rt in resolved.terminals:
                if rt.direction == "in":
                    default = getattr(rt, "default_value", None)
                    resolved_inputs[rt.index] = (rt.name, default)
                    if rt.type == "cluster" and rt.name and "error" in rt.name.lower():
                        resolver_error_indices.add(rt.index)
                    if getattr(rt, "expandable", False):
                        expandable_indices.add(rt.index)

        for term in node.terminals:
            if term.direction != "input":
                continue

            # Skip error terminals — Python uses exceptions
            # Check parser type, resolver name, AND resolver type
            if term.is_error_cluster:
                continue
            if term.index in resolver_error_indices:
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

            # Skip error inputs by resolved name
            if term_name and "error" in term_name.lower():
                continue


            # Resolve from context - None means unwired
            value = ctx.resolve(term_id)
            if value:
                # Wired terminal with -1 index: resolution failure
                if term_index == -1:
                    avail = [
                        {"index": rt.index, "name": rt.name, "type": rt.type}
                        for rt in (resolved.terminals if resolved else [])
                        if rt.direction == "in" and rt.index not in {
                            t.index for t in node.terminals if t.index >= 0
                        }
                    ]
                    raise TerminalResolutionNeeded(
                        prim_id=node.primResID or 0,
                        prim_name=node.name or "unknown",
                        terminal_direction="input",
                        terminal_type=term.lv_type.underlying_type if term.lv_type else None,
                        available=avail,
                        vi_name=ctx.vi_name,
                    )
                resolved_value = value
            elif default_value is not None:
                resolved_value = default_value
            else:
                # Unwired terminal — use default from JSON or type-based default
                if term_name and ("refnum" in term_name.lower() or "file_path" in term_name.lower()) and node.primResID in (8010, 8011, 8003, 8005):
                    vi_short = (ctx.vi_name or "output").replace(".vi", "").replace(":", "_").replace(".", "_")
                    resolved_value = f"open(Path(__file__).parent / '{vi_short}.txt', 'a+')"
                    ctx.imports.add("from pathlib import Path")
                elif term_name and "vi_path" in term_name.lower() and node.primResID in (9101,):
                    resolved_value = "Path(__file__)"
                    ctx.imports.add("from pathlib import Path")
                else:
                    tname = (term_name or "").lower()
                    ptype = term.python_type() if hasattr(term, 'python_type') else "Any"
                    if "array" in tname or ptype.startswith("list"):
                        resolved_value = "[]"
                    elif "string" in tname or ptype == "str":
                        resolved_value = "''"
                    elif "index" in tname or "offset" in tname or "count" in tname:
                        resolved_value = "0"
                    elif "path" in tname or ptype == "Path":
                        resolved_value = "Path('.')"
                        ctx.imports.add("from pathlib import Path")
                    elif "bool" in tname or ptype == "bool":
                        resolved_value = "False"
                    else:
                        resolved_value = "None"

            # Expandable terminal: collect into group by base index.
            # Expanded terminals have indices that are offset from the base
            # (e.g., base=2 for index, expanded 2D gives indices 2, 4).
            matched_expandable = False
            if expandable_indices:
                for base_idx in expandable_indices:
                    if term_index == base_idx or (
                        term_index > base_idx
                        and (term_index - base_idx) % max(len(expandable_indices), 1) == 0
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
        # Multiple groups: {name_values} per group (e.g., {index_values}, {length_values}).
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
        self,
        node: Operation,
        hint: dict[str, str],
        input_map: dict[str, str],
        ctx: CodeGenContext,
        resolved: Any,
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
                    resolved_outputs[rt.index] = rt.name

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
            if term_name and "error" in term_name.lower():
                continue

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
            substituted = self._substitute_template(expr_template, input_map, resolved)
            # Only inline simple expressions — no parens (function calls),
            # no quotes (string literals), no brackets (subscripts)
            if any(c in substituted for c in "('\"["):
                continue
            bindings[term.id] = substituted
            skip_ids.add(term.id)

        return bindings, skip_ids

    def _get_wired_outputs(
        self, node: Operation, resolved: Any, ctx: CodeGenContext,
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
                    resolved_outputs[rt.index] = rt.name
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

            # Skip error outputs by resolved name
            if term_name and "error" in term_name.lower():
                continue


            # Expandable output: accept all terminals mapped to expandable index
            if expandable_out_index is not None and term_index == expandable_out_index:
                base_name = resolved_outputs.get(expandable_out_index, "element")
                var_name = to_var_name(base_name) + f"_{len(outputs)}"
                outputs.append((term_id, term_name or base_name, var_name))
                continue

            # Output with -1 index and no name: resolution failure
            if term_index == -1 and not term_name:
                avail = [
                    {"index": rt.index, "name": rt.name, "type": rt.type}
                    for rt in (resolved.terminals if resolved else [])
                    if rt.direction == "out" and rt.index not in {
                        t.index for t in node.terminals if t.index >= 0
                    }
                ]
                raise TerminalResolutionNeeded(
                    prim_id=node.primResID or 0,
                    prim_name=node.name or "unknown",
                    terminal_direction="output",
                    terminal_type=term.lv_type.underlying_type if term.lv_type else None,
                    available=avail,
                    vi_name=ctx.vi_name if ctx else None,
                )

            var_name = ctx.make_output_var(term_name, node.id, terminal_id=term_id) if term_name else f"out_{term_index}"
            outputs.append((term_id, term_name, var_name))

        return outputs

    def _build_dict_hint(
        self,
        hint: dict[str, str],
        input_map: dict[str, str],
        wired_outputs: list[tuple[str, str, str]],
        ctx: CodeGenContext,
        resolved: Any,
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
            body_substituted = self._substitute_template(body, input_map, resolved)
            statements.append(parse_stmt(body_substituted))

        # Handle each output — match by position, not name.
        # The graph knows the literal connections; we just need
        # to pair each wired output with its expression.
        exprs = [v for k, v in hint.items() if k not in ("_body", "_import")]
        for i, (term_id, term_name, var_name) in enumerate(wired_outputs):
            if i < len(exprs):
                expr_substituted = self._substitute_template(exprs[i], input_map, resolved)
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
        self,
        hint: str,
        input_map: dict[str, str],
        wired_outputs: list[tuple[str, str, str]],
        ctx: CodeGenContext,
        resolved: Any,
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
        expr_substituted = self._substitute_template(expr, input_map, resolved)

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

    def _find_output_expr(self, hint: dict[str, str], term_name: str) -> str | None:
        """Find expression for an output terminal in hint dict."""
        if not term_name:
            return None

        # Direct match
        if term_name in hint:
            return hint[term_name]

        # Normalized match
        normalized = to_var_name(term_name).rstrip("_")
        for key, expr in hint.items():
            if key in ("_body", "_import"):
                continue
            if to_var_name(key).rstrip("_") == normalized:
                return expr

        return None

    def _substitute_template(
        self, template: str, input_map: dict[str, str], resolved: Any = None
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
            return input_map.get(key, text)

        result = re.sub(combined, _replace, template)

        # Replace any remaining unsubstituted in_N placeholders with None
        leftover = re.findall(r'\bin_\d+\b', result)
        if leftover:
            import warnings
            warnings.warn(
                f"Unresolved template placeholders: {leftover}",
                stacklevel=3,
            )
            result = re.sub(r'\bin_(\d+)\b', 'None', result)

        return result

    def _emit_unknown(
        self, node: Operation, prim_id: int, ctx: CodeGenContext
    ) -> CodeFragment:
        """Raise TerminalResolutionNeeded for unknown primitives.

        Unknown primitives MUST be resolved before generation can proceed.
        Silent placeholders hide failures — the conversion loop depends on
        errors being raised so they can be resolved one at a time.
        """
        from vipy.primitive_resolver import TerminalResolutionNeeded

        # Collect available terminal info for the diagnostic
        available = []
        for term in node.terminals:
            available.append({
                "index": term.index,
                "name": term.name,
                "type": term.lv_type.underlying_type if term.lv_type else None,
                "direction": term.direction,
            })

        raise TerminalResolutionNeeded(
            prim_id=prim_id,
            prim_name=node.name or "unknown",
            terminal_direction="unknown",
            terminal_type=None,
            available=available,
            vi_name=ctx.vi_name,
        )
