"""Code generator for SubVI calls."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from ..ast_utils import to_function_name, to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class SubVICodeGen(NodeCodeGen):
    """Generate code for SubVI calls.

    Produces: result = subvi_function(arg1, arg2, ...)
    Binds output terminals to result.field_name

    Uses vilib resolver to get proper field names for known SubVIs.
    """

    def generate(self, node: dict[str, Any], ctx: CodeGenContext) -> CodeFragment:
        """Generate code for a SubVI call."""
        subvi_name = node.get("name", "")
        if not subvi_name:
            return CodeFragment.empty()

        func_name = to_function_name(subvi_name)
        result_var = f"{func_name}_result"

        # Look up vilib info for this SubVI
        vilib_vi = self._get_vilib_vi(subvi_name)

        # Gather input arguments with proper names
        args, keywords = self._build_arguments(node, ctx, vilib_vi)

        # Build function call AST
        if keywords:
            # Use keyword arguments for clarity
            call = ast.Call(
                func=ast.Name(id=func_name, ctx=ast.Load()),
                args=[],
                keywords=[
                    ast.keyword(arg=k, value=self._to_ast_value(v))
                    for k, v in keywords.items()
                ],
            )
        else:
            # Positional arguments
            call = ast.Call(
                func=ast.Name(id=func_name, ctx=ast.Load()),
                args=[self._to_ast_value(a) for a in args],
                keywords=[],
            )

        # Assignment: result = func(...)
        stmt = ast.Assign(
            targets=[ast.Name(id=result_var, ctx=ast.Store())],
            value=call,
        )

        # Build output bindings using vilib terminal names
        bindings = self._build_output_bindings(node, result_var, vilib_vi, ctx)

        # Build import
        imports = {f"from .{func_name} import {func_name}"}

        return CodeFragment(
            statements=[stmt],
            bindings=bindings,
            imports=imports,
        )

    def _get_vilib_vi(self, subvi_name: str) -> Any | None:
        """Look up SubVI in vilib resolver."""
        try:
            from ....vilib_resolver import get_resolver
            resolver = get_resolver()
            return resolver.resolve_by_name(subvi_name)
        except Exception:
            return None

    def _build_arguments(
        self,
        node: dict[str, Any],
        ctx: CodeGenContext,
        vilib_vi: Any | None,
    ) -> tuple[list[str], dict[str, str]]:
        """Build input arguments, using vilib names if available."""
        subvi_name = node.get("name", "")

        # Build index → vilib terminal name mapping
        # Prefer python_param if available, otherwise use terminal name
        vilib_inputs: dict[int, str] = {}
        if vilib_vi:
            for vt in vilib_vi.terminals:
                if vt.direction == "in":
                    vilib_inputs[vt.index] = vt.python_param or vt.name

        args = []
        keywords: dict[str, str] = {}

        for term in node.get("terminals", []):
            if term.get("direction") != "input":
                continue

            term_id = term.get("id")
            term_index = term.get("index", 0)
            term_name = term.get("name", "")
            callee_param = term.get("callee_param_name", "")
            value = ctx.resolve(term_id)

            # Skip unwired terminals - they'll use default values
            if value is None:
                continue

            # Determine parameter name with priority:
            # 1. vilib python_param name
            # 2. Callee parameter name from connector pane mapping
            # 3. Terminal name from node
            # 4. Look up from callee VI context
            param_name = None

            if vilib_inputs and term_index in vilib_inputs:
                # vilib knows the correct parameter name
                param_name = to_var_name(vilib_inputs[term_index])
            elif callee_param:
                # Use callee parameter name from connector pane
                param_name = to_var_name(callee_param)
            elif term_name:
                # Use terminal name from node
                param_name = to_var_name(term_name)
            else:
                # Try looking up from callee VI context
                callee_name = ctx.get_callee_param_name(subvi_name, term_index)
                if callee_name:
                    param_name = to_var_name(callee_name)

            if not param_name:
                raise ValueError(
                    f"Cannot resolve parameter name for SubVI '{subvi_name}' "
                    f"terminal index={term_index}. "
                    f"No vilib, callee_param, term_name, or context lookup available."
                )

            keywords[param_name] = value

        return args, keywords

    def _build_output_bindings(
        self,
        node: dict[str, Any],
        result_var: str,
        vilib_vi: Any | None,
        ctx: "CodeGenContext",
    ) -> dict[str, str]:
        """Build output terminal bindings using vilib names."""
        subvi_name = node.get("name", "")

        # Build index → vilib terminal name mapping
        # Prefer python_param if available, otherwise use terminal name
        vilib_outputs: dict[int, str] = {}
        if vilib_vi:
            for vt in vilib_vi.terminals:
                if vt.direction == "out":
                    vilib_outputs[vt.index] = vt.python_param or vt.name

        bindings = {}
        for term in node.get("terminals", []):
            if term.get("direction") != "output":
                continue

            term_id = term.get("id")
            term_index = term.get("index", 0)
            term_name = term.get("name", "")

            # Priority: vilib name > terminal name > callee context lookup
            field = None

            if vilib_outputs and term_index in vilib_outputs:
                field = to_var_name(vilib_outputs[term_index])
            elif term_name:
                field = to_var_name(term_name)
            else:
                # Try looking up from callee VI context
                callee_name = ctx.get_callee_output_name(subvi_name, term_index)
                if callee_name:
                    field = to_var_name(callee_name)

            if not field:
                raise ValueError(
                    f"Cannot resolve output field name for SubVI '{subvi_name}' "
                    f"terminal index={term_index}. "
                    f"No vilib, term_name, or context lookup available."
                )

            bindings[term_id] = f"{result_var}.{field}"

        return bindings

    def _to_ast_value(self, value: str) -> ast.expr:
        """Convert a value string to AST expression."""
        if value == "None":
            return ast.Constant(value=None)
        # Check if it's a number
        try:
            int_val = int(value)
            return ast.Constant(value=int_val)
        except ValueError:
            pass
        try:
            float_val = float(value)
            return ast.Constant(value=float_val)
        except ValueError:
            pass
        # It's a variable reference
        return ast.Name(id=value, ctx=ast.Load())
