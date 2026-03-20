"""Code generator for SubVI calls."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from vipy.graph_types import Operation
from vipy.vilib_resolver import (
    VILibResolutionNeeded,
    derive_python_location,
    get_resolver,
)

from ..ast_utils import to_function_name, to_module_name, to_var_name
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

        # For polymorphic VIs, try variant-specific lookup first
        vilib_vi = None
        if node.poly_variant_name:
            vilib_vi = self._resolve_poly_variant(subvi_name, node, ctx)

        # Fall back to base name lookup
        if not vilib_vi:
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

        # Build import - use resolver if available, otherwise default relative
        if ctx.import_resolver:
            import_stmt = ctx.import_resolver(subvi_name)
        else:
            import_stmt = f"from .{func_name} import {func_name}"
        imports = {import_stmt}

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
            if vt.direction in ("in", "input"):
                vilib_inputs[vt.index] = param_key
            elif vt.direction in ("out", "output"):
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

        # Check for unresolved input placeholders — same as vilib resolution
        import re
        unresolved_inputs = {
            m for m in re.findall(r'\{(\w+)\}', template)
            if m not in set(vilib_outputs.values())
        }
        if unresolved_inputs:
            raise VILibResolutionNeeded(
                node.name or "",
                context=self._build_resolution_context(node, ctx, vilib_vi),
            )

        # Build ref_terminals passthrough map: output_param -> input variable
        ref_passthrough: dict[str, str] = {}
        if vilib_vi.ref_terminals:
            for out_param, passthrough_spec in vilib_vi.ref_terminals.items():
                if passthrough_spec.startswith("passthrough_from:"):
                    in_param = passthrough_spec[len("passthrough_from:"):]
                    # Find the input variable that was substituted
                    for term in node.terminals:
                        if term.direction != "input":
                            continue
                        if term.index in vilib_inputs:
                            if vilib_inputs[term.index] == in_param:
                                resolved = ctx.resolve(term.id)
                                if resolved:
                                    ref_passthrough[out_param] = resolved
                                break

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
                # Check for ref passthrough - bind to same input variable
                if param_key in ref_passthrough:
                    bindings[term_id] = ref_passthrough[param_key]
                    # Still need to replace placeholder in template
                    output_var_map[param_key] = ref_passthrough[param_key]
                else:
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

    def _resolve_poly_variant(
        self, base_name: str, node: Operation, ctx: CodeGenContext
    ) -> Any | None:
        """Resolve a polymorphic VI to its specific variant.

        Uses poly_variant_name (edit-time selection extracted from the VI's
        polySelector XML element) to look up the correct variant entry
        via poly_selector_names in the driver/vilib data.
        """
        variant = node.poly_variant_name
        if not variant:
            return None
        return get_resolver().resolve_poly_variant(base_name, variant)

    def _get_vilib_vi(
        self, subvi_name: str, node: Operation | None = None,
        ctx: CodeGenContext | None = None
    ) -> Any | None:
        """Look up SubVI in vilib resolver.

        If the VI is found but has no terminal indices, raises VILibResolutionNeeded
        with context to help resolve the indices.
        """
        try:
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
                # Auto-update vilib JSON with observed terminals
                # Filter to only wired terminals
                wired_node_terminals = [
                    term for term in node.terminals
                    if ctx and ctx.is_wired(term.id)
                ] if node else []

                if wired_node_terminals:
                    # Auto-update terminals (raises VILibConflict on conflict)
                    vi = resolver.auto_update_terminals(
                        vi_name=subvi_name,
                        wired_terminals=wired_node_terminals,
                        caller_vi=ctx.vi_name if ctx else None,
                    )
                    # Successfully updated - continue with updated VI

            return vi
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

        vilib_inputs = self._build_vilib_terminal_map(
            vilib_vi, "input",
        )

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
                raise VILibResolutionNeeded(
                    subvi_name,
                    context=self._build_resolution_context(node, ctx, vilib_vi),
                )

            # Check if this parameter is an enum typedef - generate enum reference
            final_value = self._resolve_enum_value(
                value, term, vilib_vi, ctx
            ) if vilib_vi else value

            keywords[param_name] = final_value

        return args, keywords

    def _resolve_enum_value(
        self,
        value: str,
        term: Any,
        vilib_vi: Any,
        ctx: CodeGenContext,
    ) -> str:
        """Resolve enum constant to enum reference if applicable.

        Args:
            value: The resolved value (e.g., "7")
            term: Terminal object with type info
            vilib_vi: VI entry with terminal typedef info
            ctx: Code generation context

        Returns:
            Either the original value or an enum reference like "EnumName.MEMBER"
        """
        # Check if value is already an enum reference (pre-resolved in graph)
        if '.' in value and not value.replace('.', '').replace('-', '').isdigit():
            # Extract enum class name and add import
            enum_class_name = value.split('.')[0]
            if ctx.import_resolver:
                import_stmt = ctx.import_resolver(vilib_vi.name)
                import_stmt = import_stmt.rsplit(" import ", 1)[0] + f" import {enum_class_name}"
                ctx.add_import(import_stmt)
            else:
                module = to_module_name(vilib_vi.name)
                ctx.add_import(f"from .{module} import {enum_class_name}")
            return value

        # Only process constant integers
        if not value.isdigit() and not (value.startswith('-') and value[1:].isdigit()):
            return value

        int_value = int(value)
        term_index = term.index

        # Find this terminal in vilib
        vilib_term = None
        for vt in vilib_vi.terminals:
            if vt.index == term_index and vt.direction == "input":
                vilib_term = vt
                break

        if not vilib_term or not vilib_term.type:
            return value

        # Check if this terminal references an enum typedef
        if not vilib_term.type.endswith('.ctl'):
            return value

        # Get the LVType from vilib resolver
        resolver = get_resolver()
        lv_type = resolver.resolve_type(vilib_term.type)

        if not lv_type or lv_type.kind != 'enum' or not lv_type.values:
            return value

        # Reverse lookup: find enum member with this value
        for member_name, enum_val in lv_type.values.items():
            if enum_val.value == int_value:
                # Found it! Generate enum reference
                # Derive Python location from typedef_name
                if lv_type.typedef_name:
                    package, class_name = derive_python_location(lv_type.typedef_name)
                    # Add import for this enum
                    if ctx.import_resolver:
                        # Get import statement and replace func name with enum class
                        import_stmt = ctx.import_resolver(vilib_vi.name)
                        # Replace the function import with enum class import
                        import_stmt = import_stmt.rsplit(" import ", 1)[0] + f" import {class_name}"
                        ctx.add_import(import_stmt)
                    else:
                        ctx.add_import(f"from {package} import {class_name}")
                    return f"{class_name}.{member_name}"
                return str(int_value)

        # Value not in enum - return as-is
        return value

    @staticmethod
    def _build_vilib_terminal_map(
        vilib_vi: Any | None, direction: str,
    ) -> dict[int, str]:
        """Build index → terminal name mapping from vilib VI.

        Prefers python_param if available, otherwise uses terminal name.
        """
        result: dict[int, str] = {}
        if vilib_vi:
            for vt in vilib_vi.terminals:
                if vt.direction == direction:
                    result[vt.index] = vt.python_param or vt.name
        return result

    def _build_output_bindings(
        self,
        node: Operation,
        result_var: str,
        vilib_vi: Any | None,
        ctx: CodeGenContext,
    ) -> dict[str, str]:
        """Build output terminal bindings using vilib names."""
        subvi_name = node.name or ""

        vilib_outputs = self._build_vilib_terminal_map(
            vilib_vi, "output",
        )

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
                # Output field names are required for result.field access.
                # (Unlike inputs, which can be skipped when unwired.)
                raise VILibResolutionNeeded(
                    subvi_name,
                    context=self._build_resolution_context(node, ctx, vilib_vi),
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

    def _build_resolution_context(
        self,
        node: Operation,
        ctx: CodeGenContext,
        vilib_vi: Any | None,
    ) -> dict[str, Any]:
        """Build context for VILibResolutionNeeded exception.

        Collects wire types from caller's dataflow to help resolve terminal indices.
        """
        context: dict[str, Any] = {
            "caller_vi": ctx.vi_name,
        }

        # Collect wire types from dataflow (actual indices being used)
        wire_types: list[str] = []
        for term in node.terminals:
            term_id = term.id
            term_index = term.index
            direction = term.direction or "?"

            # Get type info from terminal's lv_type
            type_info = "?"
            if hasattr(term, "lv_type") and term.lv_type:
                lv_type = term.lv_type
                if lv_type.underlying_type:
                    type_info = lv_type.underlying_type
                elif lv_type.kind:
                    type_info = lv_type.kind

            # Check if wired
            is_wired = ctx.is_wired(term_id) if term_id else False
            wired_str = "wired" if is_wired else "unwired"

            wire_types.append(
                f"idx_{term_index} ({direction}, {type_info}, {wired_str})"
            )

        context["wire_types"] = wire_types

        # Collect terminal names from vilib if available
        if vilib_vi and vilib_vi.terminals:
            terminal_names = [
                f"{t.name} (idx={t.index}, {t.direction})"
                for t in vilib_vi.terminals
            ]
            context["terminal_names"] = terminal_names

        return context
