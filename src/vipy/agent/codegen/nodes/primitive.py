"""Code generator for LabVIEW primitives."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

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

    def generate(self, node: dict[str, Any], ctx: CodeGenContext) -> CodeFragment:
        """Generate code for a primitive node."""
        from ....primitive_resolver import get_resolver

        prim_id = node.get("primResID")
        if prim_id is None:
            return CodeFragment.empty()

        # Get primitive hint
        resolver = get_resolver()
        resolved = resolver.resolve(prim_id=prim_id)

        # Check if primitive is truly unknown (no hint, unknown confidence, or comment-only hint)
        hint = resolved.python_hint if resolved else None
        is_unknown = (
            not resolved
            or not hint
            or resolved.confidence == "unknown"
            or (isinstance(hint, str) and hint.strip().startswith("#"))
        )
        if is_unknown:
            # Unknown primitive - emit explicit error
            return self._emit_unknown(node, prim_id, ctx)

        # Resolve input values from context (use resolved terminals for names)
        input_map = self._build_input_map(node, ctx, resolved)

        # Get wired output terminals
        wired_outputs = self._get_wired_outputs(node, resolved, ctx)

        # Build code based on hint type
        if isinstance(resolved.python_hint, dict):
            return self._build_dict_hint(
                resolved.python_hint, input_map, wired_outputs, ctx, resolved
            )
        else:
            return self._build_string_hint(
                resolved.python_hint, input_map, wired_outputs, ctx, resolved
            )

    def _build_input_map(
        self, node: dict[str, Any], ctx: CodeGenContext, resolved: Any
    ) -> dict[str, str]:
        """Build mapping from terminal names to resolved variable names.

        Uses primitive resolver terminal names when node terminals lack names.
        """
        input_map = {}

        # Build index → resolved terminal name mapping
        resolved_inputs: dict[int, str] = {}
        if resolved and resolved.terminals:
            for rt in resolved.terminals:
                if rt.get("direction") == "in":
                    resolved_inputs[rt.get("index", -1)] = rt.get("name", "")

        for term in node.get("terminals", []):
            if term.get("direction") != "input":
                continue

            term_id = term.get("id")
            term_index = term.get("index", -1)
            term_name = term.get("name", "")

            # Priority: node terminal name > resolved terminal name
            if not term_name and term_index in resolved_inputs:
                term_name = resolved_inputs[term_index]

            # Resolve from context - None means unwired
            value = ctx.resolve(term_id)
            if term_name:
                # Use resolved value, or "None" for unwired terminals
                resolved_value = value if value else "None"
                # Add both original and normalized names
                input_map[term_name] = resolved_value
                input_map[to_var_name(term_name)] = resolved_value

        return input_map

    def _get_wired_outputs(
        self, node: dict[str, Any], resolved: Any, ctx: CodeGenContext
    ) -> list[tuple[str, str, str]]:
        """Get list of (terminal_id, terminal_name, var_name) for wired outputs.

        Uses primitive resolver terminal names when node terminals lack names.
        """
        # Build index → resolved terminal name mapping
        resolved_outputs: dict[int, str] = {}
        if resolved and resolved.terminals:
            for rt in resolved.terminals:
                if rt.get("direction") == "out":
                    resolved_outputs[rt.get("index", -1)] = rt.get("name", "")

        outputs = []
        for term in node.get("terminals", []):
            if term.get("direction") != "output":
                continue

            term_id = term.get("id")
            term_index = term.get("index", -1)
            term_name = term.get("name", "")

            # Priority: node terminal name > resolved terminal name > generic
            if not term_name and term_index in resolved_outputs:
                term_name = resolved_outputs[term_index]

            var_name = to_var_name(term_name) if term_name else f"out_{len(outputs)}"
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

        input_map contains all terminal names mapped to either their resolved
        variable name (if wired) or "None" (if unwired).

        Note: Case-sensitive matching to avoid replacing Python builtins
        like Path when template has variables named 'path'.
        """
        import re

        result = template

        # Sort by length (longest first) to avoid partial replacements
        for name, value in sorted(input_map.items(), key=lambda x: -len(x[0])):
            if name:
                pattern = r"\b" + re.escape(name) + r"\b"
                # Case-sensitive to avoid replacing Path with path's value
                result = re.sub(pattern, value, result)

        return result

    def _emit_unknown(
        self, node: dict[str, Any], prim_id: int, ctx: CodeGenContext
    ) -> CodeFragment:
        """Emit placeholder for unknown primitive.

        In non-strict mode, emits a placeholder comment. The generated code
        will have a string literal that makes it obvious something is missing.
        """
        op_id = node.get("id", "?")
        node_name = node.get("name", "unknown")

        # Create a raise statement so it fails at runtime with clear message
        # This is better than silent None returns
        error_msg = f"Unknown primitive {prim_id} ({node_name}, node {op_id})"
        raise_stmt = ast.Raise(
            exc=ast.Call(
                func=ast.Name(id="NotImplementedError", ctx=ast.Load()),
                args=[ast.Constant(value=error_msg)],
                keywords=[],
            ),
            cause=None,
        )

        # Also emit a comment so it's visible in the code
        comment = ast.Expr(
            value=ast.Constant(value=f"# TODO: Unknown primitive {prim_id}")
        )

        return CodeFragment(statements=[comment, raise_stmt])
