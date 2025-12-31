"""Module builder - orchestrates code generation.

Assembles all components into a complete, valid Python module.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from .dataflow import DataFlowTracer
from .expressions import ExpressionBuilder
from .function import FunctionBuilder
from .imports import ImportBuilder

if TYPE_CHECKING:
    from ..context import VISignature
    from ...vilib_resolver import VILibResolver
    from ...primitive_resolver import ResolvedPrimitive


class ModuleBuilder:
    """Orchestrates code generation from VI graph.

    Produces syntactically valid Python code that passes ast.parse().
    """

    def __init__(
        self,
        vi_context: dict,
        vi_name: str,
        converted_deps: dict[str, "VISignature"] | None = None,
        vilib_resolver: "VILibResolver" | None = None,
    ):
        """Initialize module builder.

        Args:
            vi_context: Context from graph.get_vi_context()
            vi_name: Name of the VI
            converted_deps: Already-converted SubVI signatures
            vilib_resolver: Resolver for vilib VIs
        """
        self.vi_context = vi_context
        self.vi_name = vi_name
        self.converted_deps = converted_deps or {}
        self.vilib_resolver = vilib_resolver

        # Initialize components
        self.tracer = DataFlowTracer(vi_context)
        self.imports = ImportBuilder()
        self.expr_builder = ExpressionBuilder(self.tracer)
        self.func_builder = FunctionBuilder(self.tracer, self.imports)

        # Track what we've generated
        self._constants: list[tuple[str, str]] = []  # (var_name, value)
        self._operations: list[str] = []  # code lines
        self._vilib_imports: dict[str, set[str]] = {}  # module -> names to import
        # Unknown SubVIs: accumulate usages to build union signature
        # {func_name: {vi_name, usages: [{caller, inputs, outputs}, ...]}}
        self._unknown_subvis: dict[str, dict] = {}

    def build(self) -> str:
        """Build complete Python module.

        Returns:
            Valid Python source code

        Raises:
            SyntaxError: If generated code is invalid (shouldn't happen)
        """
        # Add standard imports
        self.imports.add_pathlib()
        self.imports.add_typing("Any", "NamedTuple")

        # Process inputs - register them in tracer
        self._register_inputs()

        # Process constants
        self._process_constants()

        # Process operations in topological order
        self._process_operations()

        # Add dependency imports
        for sig in self.converted_deps.values():
            self.imports.add_dependency(sig)

        # Add vilib imports (functions, result types, enums)
        for module_name, names in self._vilib_imports.items():
            for name in names:
                self.imports.add_vilib(module_name, name)

        # Add imports for unknown SubVI stubs (separate modules)
        for func_name, info in self._unknown_subvis.items():
            result_class = self._to_class_name(info["vi_name"]) + "Result"
            # Check if any usage has outputs
            has_outputs = any(len(u["outputs"]) > 0 for u in info["usages"])
            if has_outputs:
                self.imports.add_from(f".{func_name}", func_name, result_class)
            else:
                self.imports.add_from(f".{func_name}", func_name)

        # Build function definition
        body_lines = self._build_body()
        func_def = self.func_builder.build(
            self.vi_name,
            self.vi_context.get("inputs", []),
            self.vi_context.get("outputs", []),
            body_lines,
        )

        # Assemble module
        code = self._assemble(func_def)

        # Validate
        self._validate(code)

        return code

    def get_unknown_subvis(self) -> dict[str, dict]:
        """Get info about unknown SubVIs for stub generation.

        Returns:
            Dict mapping func_name -> {vi_name, usages: [{caller, inputs, outputs}]}
            The orchestrator should merge these across VIs and generate stub files.
        """
        return self._unknown_subvis

    def _register_inputs(self) -> None:
        """Register VI inputs in the tracer."""
        for inp in self.vi_context.get("inputs", []):
            inp_id = inp.get("id")
            var_name = self._to_var_name(inp.get("name", "input"))

            # Register the input ID itself (may be terminal) and child terminals
            self.tracer.register_variable(inp_id, var_name)
            for term in self.vi_context.get("terminals", []):
                if term.get("parent_id") == inp_id:
                    self.tracer.register_variable(term.get("id"), var_name)

    def _process_constants(self) -> None:
        """Process constants and register in tracer."""
        used_names: set[str] = set()  # Track used names to avoid collisions
        ops_by_id = {op.get("id"): op for op in self.vi_context.get("operations", [])}

        for i, const in enumerate(self.vi_context.get("constants", [])):
            const_id = const.get("id")
            raw_value = const.get("value", "")
            lv_type = const.get("type", "")
            const_label = const.get("label")

            # Try to resolve as enum via data flow
            enum_value = self._resolve_enum_constant(const_id, raw_value)

            if enum_value:
                var_name = self._to_var_name(enum_value.split(".")[-1].lower())
                value = enum_value
            else:
                # Derive name from: label > destination terminal > type-based fallback
                var_name = self._derive_constant_name(const_id, const_label, lv_type, ops_by_id)
                value = self._format_constant(raw_value, lv_type)

            # Ensure unique name
            base_name = var_name
            counter = 1
            while var_name in used_names:
                var_name = f"{base_name}_{counter}"
                counter += 1
            used_names.add(var_name)

            self._constants.append((var_name, value))

            # Register in tracer - constants can be terminals themselves
            # (const_id == terminal_id in data flow) OR have child terminals
            self.tracer.register_variable(const_id, var_name)
            for term in self.vi_context.get("terminals", []):
                if term.get("parent_id") == const_id:
                    self.tracer.register_variable(term.get("id"), var_name)

    def _resolve_enum_constant(self, const_id: str, raw_value: str) -> str | None:
        """Try to resolve a constant to an enum member."""
        if not self.vilib_resolver:
            return None

        # Find where this constant flows to
        for flow in self.vi_context.get("data_flow", []):
            if flow.get("from_parent_id") != const_id:
                continue

            dest_op_id = flow.get("to_parent_id")
            dest_term_id = flow.get("to_terminal_id")

            # Find the destination operation
            for op in self.vi_context.get("operations", []):
                if op.get("id") != dest_op_id:
                    continue

                if "SubVI" not in op.get("labels", []):
                    continue

                subvi_name = op.get("name", "")
                vilib_vi = self.vilib_resolver.resolve_by_name(subvi_name)
                if not vilib_vi:
                    continue

                # Find destination terminal index
                dest_term_index = None
                for term in op.get("terminals", []):
                    if term.get("id") == dest_term_id:
                        dest_term_index = term.get("index")
                        break

                if dest_term_index is None:
                    continue

                # Find vilib terminal with enum
                for vilib_term in vilib_vi.terminals:
                    if vilib_term.index == dest_term_index and vilib_term.enum:
                        enum_name = vilib_term.enum
                        enums = self.vilib_resolver.get_enums()
                        enum_def = enums.get(enum_name, {})

                        try:
                            int_value = int(raw_value)
                        except (ValueError, TypeError):
                            continue

                        for member_name, member_info in enum_def.get("values", {}).items():
                            if member_info.get("value") == int_value:
                                # Add enum to the vilib module's imports
                                module_name = self._to_function_name(subvi_name)
                                self._add_vilib_import(module_name, enum_name)
                                return f"{enum_name}.{member_name}"

        return None

    def _format_constant(self, raw_value: str, lv_type: str) -> str:
        """Format a constant as valid Python."""
        if lv_type == "String" or lv_type == "string":
            return repr(raw_value)
        if lv_type in ("Path", "path"):
            return f"Path({repr(raw_value)})"
        if lv_type in ("Boolean", "bool", "boolean"):
            return "True" if raw_value.lower() in ("true", "1") else "False"

        # Try as number
        try:
            if "." in str(raw_value):
                return str(float(raw_value))
            return str(int(raw_value))
        except (ValueError, TypeError):
            pass

        # Default to string
        if raw_value:
            return repr(raw_value)
        return "None"

    def _derive_constant_name(
        self,
        const_id: str,
        label: str | None,
        lv_type: str,
        ops_by_id: dict[str, dict],
    ) -> str:
        """Derive a meaningful name for a constant.

        Priority:
        1. Use label if present
        2. Use destination terminal name (from vilib or primitive resolver)
        3. Fall back to type-based naming
        """
        from ...primitive_resolver import get_resolver as get_primitive_resolver

        # 1. Use label if present
        if label:
            return self._to_var_name(label)

        # 2. Find destination terminal name via data flow
        for flow in self.vi_context.get("data_flow", []):
            if flow.get("from_parent_id") != const_id:
                continue

            dest_op_id = flow.get("to_parent_id")
            dest_term_id = flow.get("to_terminal_id")
            dest_op = ops_by_id.get(dest_op_id)

            if not dest_op:
                continue

            # Find destination terminal index
            dest_term_index = None
            for term in dest_op.get("terminals", []):
                if term.get("id") == dest_term_id:
                    dest_term_index = term.get("index")
                    break

            if dest_term_index is None:
                continue

            labels = dest_op.get("labels", [])

            # Try vilib for SubVI terminal names
            if "SubVI" in labels and self.vilib_resolver:
                subvi_name = dest_op.get("name", "")
                vilib_vi = self.vilib_resolver.resolve_by_name(subvi_name)
                if vilib_vi:
                    for vt in vilib_vi.terminals:
                        if vt.index == dest_term_index and vt.direction == "in":
                            return self._to_var_name(vt.name)

            # Try primitive resolver for primitive terminal names
            if "Primitive" in labels:
                prim_id = dest_op.get("primResID")
                if prim_id:
                    prim_resolver = get_primitive_resolver()
                    resolved = prim_resolver.resolve(prim_id=prim_id)
                    if resolved:
                        for t in resolved.terminals:
                            if t.get("index") == dest_term_index and t.get("direction") == "in":
                                term_name = t.get("name")
                                if term_name:
                                    return self._to_var_name(term_name)

        # 3. Fall back to type-based naming
        type_prefix = {
            "Path": "path",
            "path": "path",
            "String": "text",
            "string": "text",
            "int": "num",
            "float": "value",
            "Boolean": "flag",
            "bool": "flag",
        }
        return type_prefix.get(lv_type, "const")

    def _process_operations(self) -> None:
        """Process operations in topological order."""
        from ...primitive_resolver import get_resolver as get_primitive_resolver

        sorted_ops = self._topological_sort()

        for op in sorted_ops:
            op_id = op.get("id")
            labels = op.get("labels", [])

            # Get wired inputs with their source values
            wired_inputs = self.tracer.get_wired_inputs(op_id)
            input_values = [src_var or "None" for idx, term_id, src_var in wired_inputs]

            # Get wired outputs
            wired_outputs = self.tracer.get_wired_outputs(op_id)

            if "SubVI" in labels:
                self._process_subvi(op, input_values, wired_outputs)

            elif "Primitive" in labels:
                self._process_primitive(op, input_values, wired_outputs)

    def _process_subvi(
        self,
        op: dict,
        input_values: list[str],
        wired_outputs: list[tuple[int, str]],
    ) -> None:
        """Process a SubVI call."""
        subvi_name = op.get("name", "")
        func_name = self._to_function_name(subvi_name)
        result_var = func_name + "_result"
        is_known = False

        # Determine if we have an implementation
        if subvi_name in self.converted_deps:
            # Already converted dependency
            sig = self.converted_deps[subvi_name]
            func_name = sig.function_name
            is_known = True
        elif self.vilib_resolver:
            vilib_vi = self.vilib_resolver.resolve_by_name(subvi_name)
            if vilib_vi and vilib_vi.python_impl:
                # Add vilib imports: function, result type, and any input enums
                module_name = func_name
                result_class = self._to_class_name(subvi_name) + "Result"
                self._add_vilib_import(module_name, func_name, result_class)
                # Also add any enum types from input terminals
                for term in vilib_vi.terminals:
                    if term.enum and term.direction == "in":
                        self._add_vilib_import(module_name, term.enum)
                is_known = True

        # Track unknown SubVIs for stub generation (accumulate usages)
        if not is_known:
            # Get wired input terminal IDs
            wired_input_ids = set()
            for flow in self.vi_context.get("data_flow", []):
                if flow.get("to_parent_id") == op.get("id"):
                    wired_input_ids.add(flow.get("to_terminal_id"))
            # Get wired output terminal IDs
            wired_output_ids = {term_id for _, term_id in wired_outputs}

            # Collect only WIRED terminals with their index for union merging
            inputs = []
            outputs = []
            for term in op.get("terminals", []):
                term_id = term.get("id")
                term_name = self._to_var_name(term.get("name", "") or "")
                term_type = term.get("type", "Any")
                term_index = term.get("index", 0)

                if term.get("direction") == "input" and term_id in wired_input_ids:
                    inputs.append({"name": term_name or f"input_{term_index}", "type": term_type, "index": term_index})
                elif term.get("direction") == "output" and term_id in wired_output_ids:
                    outputs.append({"name": term_name or f"output_{term_index}", "type": term_type, "index": term_index})

            # Initialize or append to usages
            if func_name not in self._unknown_subvis:
                self._unknown_subvis[func_name] = {
                    "vi_name": subvi_name,
                    "usages": [],
                }
            self._unknown_subvis[func_name]["usages"].append({
                "caller": self.vi_name,
                "inputs": inputs,
                "outputs": outputs,
            })

        # Generate call - use keyword args for unknown SubVIs so signature can evolve
        if is_known:
            args = ", ".join(input_values)
        else:
            # Build keyword args from wired inputs
            kwarg_pairs = []
            for term in op.get("terminals", []):
                term_id = term.get("id")
                if term.get("direction") == "input" and term_id in wired_input_ids:
                    term_name = self._to_var_name(term.get("name", "") or "")
                    param_name = term_name or f"input_{term.get('index', 0)}"
                    # Find the value for this input
                    for idx, (_, wired_term_id, src_var) in enumerate(self.tracer.get_wired_inputs(op.get("id"))):
                        if wired_term_id == term_id:
                            kwarg_pairs.append((param_name, src_var or "None"))
                            break
            args = ", ".join(f"{k}={v}" for k, v in kwarg_pairs)
        self._operations.append(f"{result_var} = {func_name}({args})")

        # Register output fields
        for idx, term_id in wired_outputs:
            # Find field name from vilib or converted deps
            field_name = f"output_{idx}"
            if self.vilib_resolver:
                vilib_vi = self.vilib_resolver.resolve_by_name(subvi_name)
                if vilib_vi:
                    for vt in vilib_vi.terminals:
                        if vt.index == idx and vt.direction == "out":
                            field_name = self._to_var_name(vt.name)
                            break

            self.tracer.register_variable(term_id, f"{result_var}.{field_name}")

    def _process_primitive(
        self,
        op: dict,
        input_values: list[str],
        wired_outputs: list[tuple[int, str]],
    ) -> None:
        """Process a primitive operation."""
        from ...primitive_resolver import get_resolver as get_primitive_resolver

        prim_id = op.get("primResID")
        prim_resolver = get_primitive_resolver()
        resolved = prim_resolver.resolve(prim_id=prim_id)

        if not resolved or not resolved.python_hint:
            # Unknown primitive - generate a placeholder call
            output_var = f"p{prim_id}_0"
            args = ", ".join(input_values)
            self._operations.append(f"{output_var} = primitive_{prim_id}({args})")
            if wired_outputs:
                self.tracer.register_variable(wired_outputs[0][1], output_var)
            return

        # Get input terminal names from primitive definition
        # Match by position within inputs, not by index
        prim_inputs = [t for t in resolved.terminals if t.get("direction") == "in"]
        prim_inputs.sort(key=lambda t: t.get("index", 0))
        input_names = [t.get("name", "") for t in prim_inputs]

        # Get output info - match by position within outputs, not by index
        # Graph uses connector pane indices, primitive uses logical indices
        prim_outputs = [t for t in resolved.terminals if t.get("direction") == "out"]
        prim_outputs.sort(key=lambda t: t.get("index", 0))

        output_info = []
        for i, (idx, term_id) in enumerate(wired_outputs):
            # Match by position in sorted output list
            if i < len(prim_outputs):
                output_name = self._to_var_name(prim_outputs[i].get("name", ""))
            else:
                output_name = f"out_{i}"
            if not output_name:
                output_name = f"out_{i}"
            output_info.append((idx, term_id, output_name))

        # Build expression
        expr = self.expr_builder.build_primitive(
            resolved.python_hint,
            input_values,
            input_names,
            output_info,
        )

        # Emit pre-statements (e.g., _body from dict hints)
        if expr.pre_statements:
            self._operations.extend(expr.pre_statements)

        # Generate assignment
        if len(expr.output_vars) == 0:
            if expr.code:  # Only emit if there's code (body-only case handled above)
                self._operations.append(expr.code)
        elif len(expr.output_vars) == 1:
            self._operations.append(f"{expr.output_vars[0]} = {expr.code}")
        else:
            vars_str = ", ".join(expr.output_vars)
            self._operations.append(f"{vars_str} = {expr.code}")

    def _build_body(self) -> list[str]:
        """Build function body lines."""
        lines = []

        # Constants
        if self._constants:
            lines.append("# Constants")
            for var_name, value in self._constants:
                lines.append(f"{var_name} = {value}")
            lines.append("")

        # Operations
        if self._operations:
            lines.append("# Operations")
            lines.extend(self._operations)

        return lines

    def _assemble(self, func_def) -> str:
        """Assemble complete module."""
        lines = []

        # Module docstring
        lines.append('"""Generated Python module."""')
        lines.append("")

        # Imports
        lines.extend(self.imports.generate())
        lines.append("")

        # Result class
        class_lines = self.func_builder.render_result_class(func_def)
        if class_lines:
            lines.extend(class_lines)
            lines.append("")

        # Function signature
        lines.append(self.func_builder.render_signature(func_def))

        # Docstring
        lines.append(f'    """{func_def.docstring}"""')

        # Body
        for line in func_def.body_lines:
            lines.append(f"    {line}" if line else "")

        # Return
        lines.append("")
        lines.append(f"    {self.func_builder.render_return(func_def)}")

        return "\n".join(lines)

    def _validate(self, code: str) -> None:
        """Validate generated code is syntactically correct."""
        try:
            ast.parse(code)
        except SyntaxError as e:
            # This indicates a bug in our generation
            raise SyntaxError(f"Generated invalid Python: {e}\n\nCode:\n{code}")

    def _topological_sort(self) -> list[dict]:
        """Sort operations by data dependencies."""
        operations = self.vi_context.get("operations", [])
        op_by_id = {op.get("id"): op for op in operations}

        # Build dependency graph
        dependencies: dict[str, set[str]] = {op.get("id"): set() for op in operations}

        # Map output terminals to operations
        output_to_op: dict[str, str] = {}
        for op in operations:
            for term in op.get("terminals", []):
                if term.get("direction") == "output":
                    output_to_op[term.get("id", "")] = op.get("id")

        # Find dependencies
        for op in operations:
            op_id = op.get("id")
            for term in op.get("terminals", []):
                if term.get("direction") == "input":
                    term_id = term.get("id", "")
                    src_term = self.tracer.get_source_terminal(term_id)
                    if src_term and src_term in output_to_op:
                        dep_op_id = output_to_op[src_term]
                        if dep_op_id != op_id:
                            dependencies[op_id].add(dep_op_id)

        # Kahn's algorithm
        result = []
        in_degree = {op_id: len(deps) for op_id, deps in dependencies.items()}
        queue = [op_id for op_id, deg in in_degree.items() if deg == 0]

        while queue:
            op_id = queue.pop(0)
            if op_id in op_by_id:
                result.append(op_by_id[op_id])

            for other_id, deps in dependencies.items():
                if op_id in deps:
                    in_degree[other_id] -= 1
                    if in_degree[other_id] == 0 and other_id not in [r.get("id") for r in result]:
                        queue.append(other_id)

        # Add remaining
        for op in operations:
            if op not in result:
                result.append(op)

        return result

    def _to_function_name(self, name: str) -> str:
        """Convert VI name to Python function name."""
        name = name.replace(".vi", "").replace(".VI", "")
        if ":" in name:
            name = name.split(":")[-1]
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "vi_" + result
        return result or "vi_function"

    def _to_class_name(self, name: str) -> str:
        """Convert VI name to PascalCase class name."""
        name = name.replace(".vi", "").replace(".VI", "")
        if ":" in name:
            name = name.split(":")[-1]
        words = name.replace("-", " ").replace("_", " ").split()
        return "".join(w.capitalize() for w in words) or "VI"

    def _to_var_name(self, name: str) -> str:
        """Convert name to Python variable name."""
        if not name:
            return "value"
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "var_" + result
        return result or "value"

    def _add_vilib_import(self, module: str, *names: str) -> None:
        """Add names to import from a vilib module.

        Args:
            module: The vilib module name (e.g., "get_system_directory")
            names: Names to import (function, result type, enums)
        """
        if module not in self._vilib_imports:
            self._vilib_imports[module] = set()
        self._vilib_imports[module].update(names)

    def _generate_stubs(self) -> list[str]:
        """Generate stub functions for unknown SubVIs.

        Computes union signature from all observed usages.
        The LLM should implement these based on VI name semantics.
        """
        if not self._unknown_subvis:
            return []

        lines = []
        lines.append("# " + "=" * 60)
        lines.append("# STUBS: Unknown SubVIs - implement based on VI name semantics")
        lines.append("# " + "=" * 60)

        for func_name, info in self._unknown_subvis.items():
            vi_name = info["vi_name"]
            usages = info["usages"]

            # Compute union of inputs/outputs across all usages
            union_inputs, union_outputs, usage_summary = self._compute_union_signature(usages)

            lines.append("")
            lines.append(f"# {'-' * 40}")
            lines.append(f"# STUB: {vi_name}")
            lines.append(f"# Observed in {len(usages)} caller(s):")
            for usage_line in usage_summary:
                lines.append(f"#   {usage_line}")
            lines.append(f"# {'-' * 40}")

            # Generate result class if there are outputs
            result_class = self._to_class_name(vi_name) + "Result"
            if union_outputs:
                lines.append(f"class {result_class}(NamedTuple):")
                for out in union_outputs:
                    py_type = self.func_builder._map_type(out["type"])
                    optional_marker = "  # optional" if out.get("optional") else ""
                    lines.append(f"    {out['name']}: {py_type}{optional_marker}")
                lines.append("")

            # Generate function signature with optional params having defaults
            params = []
            for inp in union_inputs:
                py_type = self.func_builder._map_type(inp["type"])
                if inp.get("optional"):
                    params.append(f"{inp['name']}: {py_type} = None")
                else:
                    params.append(f"{inp['name']}: {py_type}")
            params_str = ", ".join(params)

            return_type = result_class if union_outputs else "None"
            lines.append(f"def {func_name}({params_str}) -> {return_type}:")
            lines.append(f'    """STUB: {vi_name}')
            lines.append("")
            lines.append("    TODO: Implement based on VI name semantics.")
            lines.append('    """')
            lines.append(f'    raise NotImplementedError("{vi_name} not yet converted")')
            lines.append("")

        return lines

    def _compute_union_signature(
        self, usages: list[dict]
    ) -> tuple[list[dict], list[dict], list[str]]:
        """Compute union of inputs/outputs from all usages.

        Returns:
            (union_inputs, union_outputs, usage_summary_lines)
        """
        # Merge by terminal index
        inputs_by_index: dict[int, dict] = {}
        outputs_by_index: dict[int, dict] = {}
        input_callers: dict[int, list[str]] = {}  # index -> callers that use it
        output_callers: dict[int, list[str]] = {}

        num_usages = len(usages)

        for usage in usages:
            caller = usage["caller"]
            for inp in usage["inputs"]:
                idx = inp["index"]
                if idx not in inputs_by_index:
                    inputs_by_index[idx] = inp.copy()
                    input_callers[idx] = []
                input_callers[idx].append(caller)
                # Prefer named over generic
                if inp["name"] and not inp["name"].startswith("input_"):
                    inputs_by_index[idx]["name"] = inp["name"]

            for out in usage["outputs"]:
                idx = out["index"]
                if idx not in outputs_by_index:
                    outputs_by_index[idx] = out.copy()
                    output_callers[idx] = []
                output_callers[idx].append(caller)
                if out["name"] and not out["name"].startswith("output_"):
                    outputs_by_index[idx]["name"] = out["name"]

        # Mark optional (not used by all callers)
        union_inputs = []
        for idx in sorted(inputs_by_index.keys()):
            inp = inputs_by_index[idx]
            inp["optional"] = len(input_callers[idx]) < num_usages
            union_inputs.append(inp)

        union_outputs = []
        for idx in sorted(outputs_by_index.keys()):
            out = outputs_by_index[idx]
            out["optional"] = len(output_callers[idx]) < num_usages
            union_outputs.append(out)

        # Ensure unique names
        seen = set()
        for i, inp in enumerate(union_inputs):
            if inp["name"] in seen or not inp["name"]:
                inp["name"] = f"input_{inp['index']}"
            seen.add(inp["name"])

        seen = set()
        for i, out in enumerate(union_outputs):
            if out["name"] in seen or not out["name"]:
                out["name"] = f"output_{out['index']}"
            seen.add(out["name"])

        # Build usage summary
        summary = []
        for usage in usages:
            in_names = [i["name"] for i in usage["inputs"]]
            out_names = [o["name"] for o in usage["outputs"]]
            summary.append(f"{usage['caller']}: ({', '.join(in_names)}) -> ({', '.join(out_names)})")

        return union_inputs, union_outputs, summary
