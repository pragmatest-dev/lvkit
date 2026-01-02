"""Code generator for SubVI calls."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from vipy.graph_types import Operation

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

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code for a SubVI call."""
        subvi_name = node.name or ""
        if not subvi_name:
            return CodeFragment.empty()

        # Look up vilib info for this SubVI
        # Raises VILibResolutionNeeded if indices are missing
        vilib_vi = self._get_vilib_vi(subvi_name, node, ctx)

        # Check for inline replacement first
        if vilib_vi and vilib_vi.python_code and vilib_vi.inline:
            return self._generate_inline(node, ctx, vilib_vi)

        func_name = to_function_name(subvi_name)
        result_var = f"{func_name}_result"

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

    def _generate_inline(
        self, node: Operation, ctx: CodeGenContext, vilib_vi: Any
    ) -> CodeFragment:
        """Generate inline Python code instead of function call.

        Uses {placeholder} syntax for both inputs and outputs:
        - Inputs: {param} replaced with wired value expression
        - Outputs: {param} replaced with unique variable name, then bound

        Example template: "{size} = len({array})"
        Becomes: "get_array_size_0_size = len(my_array)"
        With binding: size_terminal_id -> "get_array_size_0_size"
        """
        template = vilib_vi.python_code
        func_name = to_function_name(node.name or "inline")

        # Build index → param name mappings for inputs and outputs
        vilib_inputs: dict[int, str] = {}
        vilib_outputs: dict[int, str] = {}
        for vt in vilib_vi.terminals:
            param_key = vt.python_param or to_var_name(vt.name)
            if vt.direction == "in":
                vilib_inputs[vt.index] = param_key
            elif vt.direction == "out":
                vilib_outputs[vt.index] = param_key

        # Substitute input placeholders with wired values
        for term in node.terminals:
            if term.direction != "input":
                continue

            term_index = term.index
            term_id = term.id
            value = ctx.resolve(term_id)

            if term_index in vilib_inputs:
                param_key = vilib_inputs[term_index]
                placeholder = "{" + param_key + "}"
                template = template.replace(placeholder, value or "None")

        # Substitute output placeholders with unique variable names
        # and build bindings
        bindings = {}
        output_var_map: dict[str, str] = {}  # param_key -> generated var name

        for term in node.terminals:
            if term.direction != "output":
                continue

            term_index = term.index
            term_id = term.id

            if term_index in vilib_outputs:
                param_key = vilib_outputs[term_index]
                # Generate unique variable name
                var_name = f"{func_name}_{param_key}"
                output_var_map[param_key] = var_name
                bindings[term_id] = var_name
            else:
                # Output not in vilib definition - bind to None
                bindings[term_id] = "None"

        # Replace output placeholders in template
        for param_key, var_name in output_var_map.items():
            placeholder = "{" + param_key + "}"
            template = template.replace(placeholder, var_name)

        # Parse as statement(s) - supports multi-line
        try:
            parsed = ast.parse(template, mode="exec")
            statements = parsed.body
        except SyntaxError:
            # Fallback: wrap as expression statement
            parsed = ast.parse(template, mode="eval")
            statements = [ast.Expr(value=parsed.body)]

        # Build imports from vilib imports
        imports = set(vilib_vi.imports) if vilib_vi.imports else set()

        return CodeFragment(
            statements=statements,
            bindings=bindings,
            imports=imports,
        )

    def _get_vilib_vi(
        self, subvi_name: str, node: Operation | None = None,
        ctx: CodeGenContext | None = None
    ) -> Any | None:
        """Look up SubVI in vilib resolver.

        If the VI is found but has no terminal indices, raises VILibResolutionNeeded
        with context to help resolve the indices.
        """
        try:
            from ....vilib_resolver import VILibResolutionNeeded, get_resolver
            resolver = get_resolver()
            vi = resolver.resolve_by_name(subvi_name)

            if vi is None:
                return None

            # Check if caller's WIRED terminals are missing indices
            # Only require indices for terminals that have actual wires
            has_terminals = bool(vi.terminals)
            if has_terminals and node and ctx:
                # Get indices the caller is actually wiring (not just connector slots)
                caller_indices = set()
                for term in node.terminals:
                    term_id = term.id
                    term_index = term.index
                    # Only count terminals that have wires connected
                    if term_id and term_index is not None and ctx.is_wired(term_id):
                        caller_indices.add(term_index)
                # Get indices we have in vilib
                vilib_indices = {t.index for t in vi.terminals if t.index is not None}
                # Missing = caller wires indices we don't have mapped
                missing_indices = bool(caller_indices - vilib_indices)
            else:
                missing_indices = False

            if missing_indices:
                # Collect terminal observations before raising exception
                from ....terminal_collector import get_collector
                collector = get_collector()

                vilib_terminals = [
                    {"name": t.name, "direction": t.direction, "type": t.type}
                    for t in vi.terminals
                ]

                # Filter to only wired terminals for observation
                wired_node_terminals = [
                    term for term in node.terminals
                    if ctx and ctx.is_wired(term.id)
                ] if node else []

                if node and wired_node_terminals:
                    # Record observation from caller's dataflow (wired only)
                    collector.observe(
                        vi_name=subvi_name,
                        caller_vi="unknown",  # Could be set by caller context
                        node_terminals=wired_node_terminals,
                        vilib_terminals=vilib_terminals,
                    )

                # Build context from the node for resolution
                context = {
                    "caller_vi": "unknown",  # Will be set by caller if available
                    "terminal_names": [t.name for t in vi.terminals],
                    "pdf_data": {
                        "page": vi.page,
                        "category": vi.category,
                        "description": "",
                        "terminals": vilib_terminals,
                    },
                }

                # Add wire info from node (wired terminals only)
                if wired_node_terminals:
                    wire_types = []
                    for term in wired_node_terminals:
                        term_name = term.name or f"idx_{term.index}"
                        term_dir = term.direction
                        wire_types.append(f"{term_name} ({term_dir})")
                    context["wire_types"] = wire_types

                raise VILibResolutionNeeded(subvi_name, context)

            return vi
        except VILibResolutionNeeded:
            # Re-raise resolution exceptions - they should propagate up
            raise
        except ImportError:
            return None

    def _build_arguments(
        self,
        node: Operation,
        ctx: CodeGenContext,
        vilib_vi: Any | None,
    ) -> tuple[list[str], dict[str, str]]:
        """Build input arguments, using vilib names if available."""
        subvi_name = node.name or ""

        # Build index → vilib terminal name mapping
        # Prefer python_param if available, otherwise use terminal name
        vilib_inputs: dict[int, str] = {}
        if vilib_vi:
            for vt in vilib_vi.terminals:
                if vt.direction == "in":
                    vilib_inputs[vt.index] = vt.python_param or vt.name

        args = []
        keywords: dict[str, str] = {}

        for term in node.terminals:
            if term.direction != "input":
                continue

            term_id = term.id
            term_index = term.index
            term_name = term.name or ""
            callee_param = term.callee_param_name or ""
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
        node: Operation,
        result_var: str,
        vilib_vi: Any | None,
        ctx: CodeGenContext,
    ) -> dict[str, str]:
        """Build output terminal bindings using vilib names."""
        subvi_name = node.name or ""

        # Build index → vilib terminal name mapping
        # Prefer python_param if available, otherwise use terminal name
        vilib_outputs: dict[int, str] = {}
        if vilib_vi:
            for vt in vilib_vi.terminals:
                if vt.direction == "out":
                    vilib_outputs[vt.index] = vt.python_param or vt.name

        bindings = {}
        for term in node.terminals:
            if term.direction != "output":
                continue

            term_id = term.id
            term_index = term.index
            term_name = term.name or ""

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
