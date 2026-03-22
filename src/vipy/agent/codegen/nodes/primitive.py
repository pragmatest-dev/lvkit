"""Code generator for LabVIEW primitives."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from vipy.graph_types import Operation

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
        if prim_id is None:
            return CodeFragment.empty()

        # Get primitive hint
        resolver = get_resolver()
        resolved = resolver.resolve(prim_id=prim_id)

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

        # Get wired output terminals
        wired_outputs = self._get_wired_outputs(node, resolved, ctx)

        # Build code based on code type
        if isinstance(resolved.python_code, dict):
            fragment = self._build_dict_hint(
                resolved.python_code, input_map, wired_outputs, ctx, resolved
            )
        else:
            fragment = self._build_string_hint(
                resolved.python_code, input_map, wired_outputs, ctx, resolved
            )

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

        # Build index → (name, default_value) dict from resolved terminals
        resolved_inputs: dict[int, tuple[str, str | None]] = {}
        if resolved and resolved.terminals:
            for rt in resolved.terminals:
                if rt.direction == "in":
                    default = getattr(rt, "default_value", None)
                    resolved_inputs[rt.index] = (rt.name, default)

        for term in node.terminals:
            if term.direction != "input":
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
                resolved_value = value
            elif default_value is not None:
                resolved_value = default_value
            else:
                resolved_value = "None"

            # Add index-based key so templates can use in_1, in_2 etc.
            input_map[f"in_{term_index}"] = resolved_value

            if term_name:
                input_map[term_name] = resolved_value
                input_map[to_var_name(term_name)] = resolved_value

        return input_map

    def _get_wired_outputs(
        self, node: Operation, resolved: Any, ctx: CodeGenContext
    ) -> list[tuple[str, str, str]]:
        """Get list of (terminal_id, terminal_name, var_name) for wired outputs.

        Matches by connector pane index (sparse dict lookup).
        Terminal names in the primitive JSON should be valid Python identifiers.
        """
        # Build index → name dict from resolved terminals
        resolved_outputs: dict[int, str] = {}
        if resolved and resolved.terminals:
            for rt in resolved.terminals:
                if rt.direction == "out":
                    resolved_outputs[rt.index] = rt.name

        outputs = []
        for term in node.terminals:
            if term.direction != "output":
                continue

            # Skip error cluster outputs — Python uses exceptions
            if term.is_error_cluster:
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

            var_name = to_var_name(term_name) if term_name else f"out_{term_index}"
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

        # Handle _body (side effect statement)
        body = hint.get("_body")
        if body:
            body_substituted = self._substitute_template(body, input_map, resolved)
            statements.append(parse_stmt(body_substituted))

        # Handle each output
        for term_id, term_name, var_name in wired_outputs:
            # Find matching expression in hint
            expr = self._find_output_expr(hint, term_name)

            if expr:
                expr_substituted = self._substitute_template(expr, input_map, resolved)
                expr_ast = parse_expr(expr_substituted)
                statements.append(build_assign(var_name, expr_ast))
                bindings[term_id] = var_name
            else:
                # No hint for this output - placeholder
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
            if key == "_body":
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
        """
        import re

        result = template

        # Sort by length (longest first) to avoid partial replacements
        for name, value in sorted(input_map.items(), key=lambda x: -len(x[0])):
            if name:
                pattern = r"\b" + re.escape(name) + r"\b"
                result = re.sub(pattern, lambda m: value, result)

        return result

    def _emit_unknown(
        self, node: Operation, prim_id: int, ctx: CodeGenContext
    ) -> CodeFragment:
        """Emit placeholder for unknown primitive.

        Emits a pass-through comment so downstream operations still work.
        LabVIEW primitives always produce outputs even if we can't translate
        them — using raise would break the dataflow for everything after.
        """
        node_name = node.name or "unknown"

        # Emit a TODO comment (as string literal) so it's visible
        comment = ast.Expr(
            value=ast.Constant(
                value=f"# TODO: Unknown primitive {prim_id} ({node_name})"
            )
        )

        # Bind outputs to None so downstream operations can resolve them
        bindings: dict[str, str] = {}
        for term in node.terminals:
            if term.direction == "output":
                bindings[term.id] = "None"

        return CodeFragment(statements=[comment], bindings=bindings)
