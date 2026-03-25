"""Main conversion agent loop implementation."""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..enum_resolver import EnumResolver
from ..llm import generate_code
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .codegen import ClassBuilder, ClassConfig
from .context import VISignature
from .context_builder import ContextBuilder
from .enums import EnumRegistry
from .loop_config import ConversionConfig, ConversionResult
from .primitives import PrimitiveRegistry
from .state import ConversionState, get_progress
from .types import SharedTypeRegistry
from .validator import CodeValidator, ValidatorConfig

if TYPE_CHECKING:
    from ..graph import VIGraph
    from ..memory_graph import InMemoryVIGraph
    from ..structure import LVClass, LVLibrary


class ConversionAgent:
    """Agent loop for converting LabVIEW VIs to validated Python code.

    The agent:
    1. Processes VIs in dependency order (leaves first)
    2. Generates Python code via LLM
    3. Validates (syntax, imports, types)
    4. Retries with error feedback on failure
    5. Tracks state for SubVI imports

    Usage:
        agent = ConversionAgent(graph, config)
        results = agent.convert_all()

    Future considerations:
    - Streaming data patterns: Some VIs stream data to indicators
      (e.g., indicators inside while loops). These would need async
      generators or reactive callbacks for proper NiceGUI integration.
    """

    def __init__(
        self,
        graph: "VIGraph | InMemoryVIGraph",
        config: ConversionConfig,
    ) -> None:
        self.graph = graph
        self.config = config
        self.state = ConversionState()
        self.type_registry = SharedTypeRegistry()
        self.primitive_registry = PrimitiveRegistry()
        self.enum_registry = EnumRegistry()
        self.vilib_resolver = get_vilib_resolver()

        # Initialize validator
        validator_config = ValidatorConfig(
            output_dir=config.output_dir,
            check_syntax=config.validate_syntax,
            check_imports=config.validate_imports,
            check_types=config.validate_types,
        )
        self.validator = CodeValidator(validator_config)

    def convert_all(self) -> list[ConversionResult]:
        """Convert all VIs in dependency order.

        Returns:
            List of ConversionResult for each VI
        """
        # Clean output directory before starting
        if self.config.output_dir.exists():
            print(f"Cleaning output directory: {self.config.output_dir}", flush=True)
            shutil.rmtree(self.config.output_dir)
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        # Pre-analysis: discover types and primitives
        self._run_pre_analysis()

        # Get conversion order (leaves first)
        order = self.graph.get_conversion_order()
        results: list[ConversionResult] = []

        print(f"Converting {len(order)} VIs in dependency order...", flush=True)

        for i, vi_name in enumerate(order, 1):
            print(f"[{i}/{len(order)}] Converting: {vi_name}", flush=True)

            result = self.convert_vi(vi_name)
            results.append(result)

            if result.success:
                library_name = self._to_library_name(vi_name)
                self.state.mark_converted(vi_name, result.output_path, library_name)
                print(f"  ✓ Success ({result.attempts} attempt(s))")
            else:
                self.state.mark_failed(vi_name)
                print(f"  ✗ Failed after {result.attempts} attempts")
                for error in result.errors[:3]:
                    print(f"    - {error}")
                # Stop on failure - dependent VIs will also fail
                print("  Stopping due to failed conversion.")
                break

        # Generate package __init__.py
        self._generate_package_init(results)

        # Generate UI package __init__.py if UI generation is enabled
        if self.config.generate_ui:
            self._generate_ui_package_init(results)
            self._generate_ui_app(results)

        # Print summary
        progress = get_progress(self.state, len(order))
        print(f"\nConversion complete: {progress.converted}/{progress.total} succeeded")
        print(f"  Success rate: {progress.success_rate:.1f}%")

        return results

    def convert_vi(self, vi_name: str) -> ConversionResult:
        """Convert a single VI with retry loop.

        Args:
            vi_name: Name of the VI to convert

        Returns:
            ConversionResult with success/failure info
        """
        # Check if this is a stub VI (missing dependency)
        if self.graph.is_stub_vi(vi_name):
            return self._generate_stub_vi(vi_name)

        # Check if this is a polymorphic wrapper VI
        if self.graph.is_polymorphic(vi_name):
            return self._generate_polymorphic_vi(vi_name)

        # Get VI context from graph - structured data for LLM
        vi_context = self.graph.get_vi_context(vi_name)

        # Get converted SubVI signatures (with library-relative imports)
        subvi_sigs = self._get_subvi_signatures(vi_context, vi_name)

        # Get relevant primitives
        primitive_names = self.primitive_registry.get_primitive_names(vi_name)
        primitive_context = self.primitive_registry.get_primitive_context(vi_name)

        # Use AST-based code generation directly
        from .codegen import build_module
        import time as _time

        _start = _time.time()
        try:
            code = build_module(vi_context, vi_name, graph=self.graph)
            from dataclasses import dataclass

            @dataclass
            class _StrategyResult:
                code: str
                success: bool
                error: str | None = None
                time_seconds: float = 0.0
                attempts: int = 1

            result = _StrategyResult(code=code, success=True, time_seconds=_time.time() - _start)
        except Exception as e:
            result = _StrategyResult(code="", success=False, error=str(e), time_seconds=_time.time() - _start)

        # Suppress unused variable warnings
        _ = (subvi_sigs, primitive_names, primitive_context)

        if result.success:
            # Success - write to file
            output_path = self._write_vi_module(vi_name, result.code)

            # Optionally generate UI wrapper
            ui_path = None
            if self.config.generate_ui:
                # Extract function signature from generated code
                func_name, func_inputs, _, func_enums = self._extract_function_signature(result.code)
                # Use output names from VI context (not generic result_0, result_1)
                func_outputs = self._get_vi_outputs(vi_context)
                if func_name is not None:  # Valid function found
                    ui_path = self._generate_ui_wrapper(vi_name, func_name, func_inputs, func_outputs, func_enums)

            return ConversionResult(
                vi_name=vi_name,
                python_code=result.code,
                output_path=output_path,
                success=True,
                attempts=result.attempts,
                ui_path=ui_path,
            )

        # Strategy failed
        errors = result.errors

        # Max retries exceeded - try agentic mode if enabled
        if self.config.use_agentic_fallback:
            print("    Standard conversion failed, trying agentic mode...")
            agentic_result = self._try_agentic_conversion(
                vi_name, vi_context, subvi_sigs, primitive_names, primitive_context
            )
            if agentic_result.success:
                output_path = self._write_vi_module(vi_name, agentic_result.python_code)
                return ConversionResult(
                    vi_name=vi_name,
                    python_code=agentic_result.python_code,
                    output_path=output_path,
                    success=True,
                    attempts=self.config.max_retries + agentic_result.attempts,
                )
            errors = agentic_result.errors

        return ConversionResult(
            vi_name=vi_name,
            python_code=result.code,  # Use the code from failed strategy attempt
            output_path=None,
            success=False,
            errors=errors,
            attempts=self.config.max_retries,
        )

    def _generate_stub_vi(self, vi_name: str) -> ConversionResult:
        """Generate a stub function for a missing SubVI dependency.

        If the VI is a known vilib VI with an implementation, use that.
        Otherwise creates a function that raises NotImplementedError.
        """
        # Check if this is a known vilib VI with implementation
        if self.vilib_resolver.has_implementation(vi_name):
            code = self.vilib_resolver.get_implementation(vi_name)
            output_path = self._write_vi_module(vi_name, code)

            # Generate UI wrapper if enabled
            ui_path = None
            if self.config.generate_ui:
                func_name, func_inputs, func_outputs, func_enums = self._extract_function_signature(code)
                # Also get enum info from vilib context - match by type annotation
                vilib_enums = self._get_vilib_enums_by_type(vi_name, func_inputs)
                func_enums.update(vilib_enums)
                if func_name is not None:
                    ui_path = self._generate_ui_wrapper(vi_name, func_name, func_inputs, func_outputs, func_enums)

            return ConversionResult(
                vi_name=vi_name,
                python_code=code,
                output_path=output_path,
                success=True,
                errors=[],
                attempts=1,
                is_stub=False,  # Not a stub - real implementation
                ui_path=ui_path,
            )

        stub_info = self.graph.get_stub_vi_info(vi_name)

        func_name = self._to_function_name(vi_name)
        input_types = stub_info.input_types if stub_info else []
        output_types = stub_info.output_types if stub_info else []

        # Filter out Void types and generate meaningful parameter names
        params = []
        type_counts: dict[str, int] = {}
        for typ in input_types:
            if typ == "Void":
                continue
            param_type = self._type_to_python(typ)
            # Generate meaningful name based on type
            base_name = self._type_to_param_name(typ)
            count = type_counts.get(base_name, 0)
            type_counts[base_name] = count + 1
            param_name = base_name if count == 0 else f"{base_name}{count + 1}"
            params.append(f"{param_name}: {param_type}")

        # Filter Void from outputs and build return type
        real_outputs = [t for t in output_types if t != "Void"]
        if not real_outputs:
            return_type = "None"
        elif len(real_outputs) == 1:
            return_type = self._type_to_python(real_outputs[0])
        else:
            types_str = ", ".join(self._type_to_python(t) for t in real_outputs)
            return_type = f"tuple[{types_str}]"

        # Generate stub code
        params_str = ", ".join(params) if params else ""
        code = f'''"""Stub for missing SubVI: {vi_name}."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def {func_name}({params_str}) -> {return_type}:
    """Stub for missing SubVI: {vi_name}.

    This SubVI was not found during conversion.
    Implement this function or provide the original VI.
    """
    raise NotImplementedError("Missing SubVI: {vi_name}")
'''

        # Write to file
        output_path = self._write_vi_module(vi_name, code)

        # Generate UI wrapper if enabled
        ui_path = None
        if self.config.generate_ui:
            func_name_parsed, func_inputs, func_outputs, func_enums = self._extract_function_signature(code)
            if func_name_parsed is not None:
                ui_path = self._generate_ui_wrapper(vi_name, func_name_parsed, func_inputs, func_outputs, func_enums)

        return ConversionResult(
            vi_name=vi_name,
            python_code=code,
            output_path=output_path,
            success=True,
            errors=[],
            attempts=1,
            is_stub=True,
            ui_path=ui_path,
        )

    def _generate_polymorphic_vi(self, vi_name: str) -> ConversionResult:
        """Generate a dispatch function for a polymorphic VI.

        Polymorphic VIs are wrappers that dispatch to type-specific variants.
        We generate a Python function that checks input types at runtime
        and calls the appropriate variant.
        """
        variants = self.graph.get_poly_variants(vi_name)
        if not variants:
            # No variants found - treat as regular VI
            return self._convert_regular_vi(vi_name)

        func_name = self._to_function_name(vi_name)

        # Get signature from the first variant (all variants should have same structure)
        first_variant = variants[0]
        variant_ctx = self.graph.get_vi_context(first_variant)

        # Extract inputs and outputs from variant
        inputs = variant_ctx.inputs
        outputs = variant_ctx.outputs

        # Build parameter list from first variant
        params = []
        for inp in inputs:
            param_name = self._to_param_name(inp.name)
            params.append(f"{param_name}: Any")

        params_str = ", ".join(params) if params else ""

        # Build return type
        if not outputs:
            return_type = "None"
        elif len(outputs) == 1:
            return_type = "Any"
        else:
            return_type = f"tuple[{', '.join(['Any'] * len(outputs))}]"

        # Build dispatch logic based on variant names
        # Common patterns: "Arrays" variants handle lists, "Traditional" handle scalars
        dispatch_cases = []
        variant_imports = []

        for variant in variants:
            variant_func = self._to_function_name(variant)
            variant_imports.append(
                f"from .{self._to_module_name(variant)} import {variant_func}"
            )

            # Infer dispatch condition from variant name
            variant_lower = variant.lower()
            if "array" in variant_lower or "1d" in variant_lower:
                # Array variant - check if first input is a list
                if params:
                    first_param = self._to_param_name(inputs[0].name)
                    condition = f"isinstance({first_param}, list)"
                else:
                    condition = "False"
            elif "path" in variant_lower and "string" not in variant_lower:
                # Path variant
                if params:
                    first_param = self._to_param_name(inputs[0].name)
                    condition = f"isinstance({first_param}, Path)"
                else:
                    condition = "False"
            else:
                # Default/Traditional variant - use as fallback
                condition = None  # Will be the else clause

            dispatch_cases.append((condition, variant_func, variant))

        # Build dispatch code - conditions first, then default
        dispatch_code = []
        default_case = None

        for condition, variant_func, variant in dispatch_cases:
            call_args = ", ".join(self._to_param_name(inp.name) for inp in inputs)
            if condition is None:
                default_case = (variant_func, call_args)
            else:
                if not dispatch_code:
                    dispatch_code.append(f"    if {condition}:")
                else:
                    dispatch_code.append(f"    elif {condition}:")
                dispatch_code.append(f"        return {variant_func}({call_args})")

        # Add default case
        if default_case:
            variant_func, call_args = default_case
            if dispatch_code:
                dispatch_code.append("    else:")
                dispatch_code.append(f"        return {variant_func}({call_args})")
            else:
                # Only one variant - just call it directly
                dispatch_code.append(f"    return {variant_func}({call_args})")
        elif dispatch_code:
            # No default - add error for unhandled types
            dispatch_code.append("    else:")
            first_param = self._to_param_name(inputs[0].name) if inputs else "input"
            dispatch_code.append(
                f'        raise TypeError(f"No variant handles type: {{type({first_param})}}")'
            )

        imports_str = "\n".join(variant_imports)
        dispatch_str = "\n".join(dispatch_code)

        code = f'''"""Polymorphic dispatcher for: {vi_name}."""

from __future__ import annotations

from pathlib import Path
from typing import Any

{imports_str}


def {func_name}({params_str}) -> {return_type}:
    """Polymorphic VI - dispatches to type-specific variant.

    Variants:
{chr(10).join(f"    - {v}" for v in variants)}
    """
{dispatch_str}
'''

        # Write to file
        output_path = self._write_vi_module(vi_name, code)

        return ConversionResult(
            vi_name=vi_name,
            python_code=code,
            output_path=output_path,
            success=True,
            errors=[],
            attempts=1,
            is_stub=False,
        )

    def _convert_regular_vi(self, vi_name: str) -> ConversionResult:
        """Fallback to normal conversion for non-polymorphic VI."""
        # This shouldn't happen but handle gracefully
        vi_context = self.graph.get_vi_context(vi_name)
        subvi_sigs = self._get_subvi_signatures(vi_context, vi_name)
        primitive_names = self.primitive_registry.get_primitive_names(vi_name)
        primitive_context = self.primitive_registry.get_primitive_context(vi_name)

        from .codegen import build_module

        try:
            code = build_module(vi_context, vi_name, graph=self.graph)
            result_success = True
            result_code = code
            result_error = None
        except Exception as e:
            result_success = False
            result_code = ""
            result_error = str(e)

        if result_success:
            output_path = self._write_vi_module(vi_name, result_code)
            return ConversionResult(
                vi_name=vi_name,
                python_code=result_code,
                output_path=output_path,
                success=True,
                attempts=1,
            )

        return ConversionResult(
            vi_name=vi_name,
            python_code=result_code,
            output_path=None,
            success=False,
            errors=[result_error] if result_error else [],
            attempts=1,
        )

    def _to_param_name(self, name: str | None) -> str:
        """Convert VI parameter name to valid Python parameter name."""
        if not name:
            return "arg"
        # Convert to snake_case and remove invalid chars
        import re
        name = re.sub(r'[^\w\s]', '', name.lower())
        name = re.sub(r'\s+', '_', name)
        if name and name[0].isdigit():
            name = f"param_{name}"
        return name or "arg"

    def _to_module_name(self, vi_name: str) -> str:
        """Convert VI name to module name (file path)."""
        # Use the same logic as _write_vi_module
        return self._to_function_name(vi_name)

    def _type_to_param_name(self, lv_type: str) -> str:
        """Generate a meaningful parameter name from LabVIEW type."""
        name_map = {
            "Path": "path",
            "String": "text",
            "Boolean": "flag",
            "NumInt32": "value",
            "NumInt16": "value",
            "NumInt8": "value",
            "NumUInt32": "value",
            "NumUInt16": "value",
            "NumUInt8": "value",
            "NumFloat64": "value",
            "NumFloat32": "value",
            "Array": "items",
            "Cluster": "data",
            "TypeDef": "config",
        }
        return name_map.get(lv_type, "arg")

    def _type_to_python(self, lv_type: str) -> str:
        """Convert LabVIEW type to Python type hint."""
        type_map = {
            "Path": "Path",
            "String": "str",
            "Boolean": "bool",
            "NumInt32": "int",
            "NumInt16": "int",
            "NumInt8": "int",
            "NumUInt32": "int",
            "NumUInt16": "int",
            "NumUInt8": "int",
            "NumFloat64": "float",
            "NumFloat32": "float",
            "Array": "list",
            "Cluster": "dict",
            "Void": "None",  # Unwired terminal
            "TypeDef": "Any",  # Custom type definition - use Any until resolved
            "Function": "Any",  # Function reference
        }
        return type_map.get(lv_type, "Any")

    def convert_lvclass(self, lvclass: "LVClass") -> ConversionResult:
        """Convert a LabVIEW class to a Python class.

        Uses AST-based ClassBuilder for code generation. The class methods
        can call SubVIs both inside and outside the class - class membership
        is determined by the lvclass file (like lvlib membership).

        Args:
            lvclass: Parsed LVClass object

        Returns:
            ConversionResult for the class
        """
        module_name = self._to_module_name(lvclass.name)

        # 1. Load method VIs into graph to get their contexts
        method_contexts: dict[str, dict] = {}
        loaded_vis = set(self.graph.list_vis())

        for method in lvclass.methods:
            vi_path = lvclass.path.parent / method.vi_path
            if vi_path.exists():
                try:
                    # Load VI if not already loaded
                    vi_name = vi_path.name
                    if vi_name not in loaded_vis:
                        self.graph.load_vi(vi_path, expand_subvis=False)
                        loaded_vis.add(vi_name)

                    # Get VI context
                    ctx = self.graph.get_vi_context(vi_name)
                    if ctx:
                        method_contexts[method.name] = ctx
                except Exception as e:
                    print(f"    Warning: Could not load method VI {method.name}: {e}")

        # 2. Build class module using ClassBuilder
        config = ClassConfig(include_docstrings=True)
        builder = ClassBuilder(config=config)
        module = builder.build_class_module(
            lvclass,
            method_contexts=method_contexts,
            parent_class_name=lvclass.parent_class,
        )

        # 3. Generate code
        ast.fix_missing_locations(module)
        code = ast.unparse(module)

        # 4. Validate
        validation = self.validator.validate(code, module_name)

        if validation.is_valid:
            output_path = self.config.output_dir / f"{module_name}.py"
            output_path.write_text(code)

            return ConversionResult(
                vi_name=lvclass.name,
                python_code=code,
                output_path=output_path,
                success=True,
            )

        return ConversionResult(
            vi_name=lvclass.name,
            python_code=code,
            output_path=None,
            success=False,
            errors=[e.message for e in validation.errors],
        )

    def convert_lvlib(self, lvlib: "LVLibrary") -> list[ConversionResult]:
        """Convert a LabVIEW library to a Python package.

        Args:
            lvlib: Parsed LVLibrary object

        Returns:
            List of ConversionResults for each member
        """
        lib_name = self._to_module_name(lvlib.name)
        lib_dir = self.config.output_dir / lib_name
        lib_dir.mkdir(parents=True, exist_ok=True)

        results: list[ConversionResult] = []

        # Convert each member VI
        for member in lvlib.members:
            if member.member_type == "VI":
                result = self.convert_vi(member.name)
                results.append(result)

        # Generate library __init__.py
        self._generate_library_init(lib_dir, results)

        # Generate library-specific types.py
        self.type_registry.generate_types_file(
            self.config.output_dir,
            f"library:{lvlib.name}",
        )

        return results

    def _run_pre_analysis(self) -> None:
        """Run pre-analysis pass to discover types, primitives, and enums."""
        print("Running pre-analysis...")

        # Discover from graph
        self.type_registry.discover_from_graph(self.graph)
        self.primitive_registry.discover_from_graph(self.graph)
        self.enum_registry.discover_from_graph(self.graph)

        # Generate types.py files
        types_files = self.type_registry.generate_all_types_files(self.config.output_dir)
        if types_files:
            print(f"  Generated {len(types_files)} types.py file(s)")

        # Generate primitives package (only for non-inline primitives)
        prims = self.primitive_registry.get_all_primitives()
        if prims:
            self.primitive_registry.generate_primitives_package(
                self.config.output_dir,
                self.config.llm_config,
            )

        # Report discovered enums
        enum_stats = self.enum_registry.stats()
        if enum_stats["enum_count"] > 0:
            print(f"  Discovered {enum_stats['enum_count']} enum(s) in {enum_stats['vis_with_enums']} VI(s)")

    def _extract_io(
        self,
        io_list: list[dict],
    ) -> list[tuple[str, str]]:
        """Extract (name, type) tuples from IO list."""
        result = []
        for item in io_list:
            name = item.get("name", "unknown")
            typ = item.get("type", "Any")
            result.append((name, self._map_type(typ)))
        return result

    def _get_vi_outputs(self, vi_context: object) -> list[tuple[str, str]]:
        """Get output names and types from VI context.

        Uses the actual output names from the VI (e.g., "Settings Path", "error out")
        rather than generic names like "result_0", "result_1".
        """
        outputs = vi_context.outputs
        result = []
        for out in outputs:
            # Terminal dataclass has name and type attributes
            name = out.name or "result"
            typ = out.python_type()
            result.append((name, self._map_type(typ)))
        return result

    def _get_subvi_signatures(
        self,
        vi_context: object,
        from_vi_name: str,
    ) -> dict[str, VISignature]:
        """Get signatures for already-converted SubVIs.

        Args:
            vi_context: VIContext with subvi_calls and operations
            from_vi_name: Name of the VI doing the importing (for relative imports)
        """
        signatures = {}
        from_library = self._to_library_name(from_vi_name)

        # Check subvi_calls (SubVIs with CALLS relationship to VI nodes)
        for subvi in vi_context.subvi_calls:
            # subvi_calls uses "vi_name" key (the target VI name)
            subvi_name = subvi.get("vi_name", "")
            if subvi_name and self.state.is_converted(subvi_name):
                module = self.state.get_module(subvi_name)
                if module:
                    signatures[subvi_name] = VISignature(
                        name=subvi_name,
                        module_name=module.module_name,
                        function_name=module.exports[0] if module.exports else "",
                        signature=module.signature,
                        import_statement=self.state.get_import_statement(
                            subvi_name, from_library=from_library
                        ),
                    )

        # Also check operations for SubVIs (handles stub/vilib VIs without CALLS relationships)
        for op in vi_context.operations:
            # Operation dataclass has labels and name attributes
            if "SubVI" in op.labels:
                subvi_name = op.name or ""
                # Skip if already found via subvi_calls or not converted
                if subvi_name and subvi_name not in signatures and self.state.is_converted(subvi_name):
                    module = self.state.get_module(subvi_name)
                    if module:
                        signatures[subvi_name] = VISignature(
                            name=subvi_name,
                            module_name=module.module_name,
                            function_name=module.exports[0] if module.exports else "",
                            signature=module.signature,
                            import_statement=self.state.get_import_statement(
                                subvi_name, from_library=from_library
                            ),
                        )

        return signatures

    def _get_primitive_mappings(self, vi_context: object) -> dict[int, str]:
        """Get primResID -> function name mappings for a VI.

        Returns empty dict - primitive behavior is inferred by LLM from graph context
        (terminal types, connections, data flow). No hardcoded mappings.
        """
        # Previously used KNOWN_PRIMITIVES, now LLM infers from context
        return {}

    def _write_vi_module(self, vi_name: str, code: str) -> Path:
        """Write VI code to a module file in the appropriate library folder."""
        output_path, library_name = self._get_module_path(vi_name)

        # Ensure library __init__.py exists
        if library_name:
            init_path = output_path.parent / "__init__.py"
            if not init_path.exists():
                init_path.write_text(f'"""Package for {library_name} library."""\n')

        output_path.write_text(code)
        return output_path

    def _generate_ui_wrapper(
        self,
        vi_name: str,
        function_name: str,
        inputs: list[tuple[str, str]],
        outputs: list[tuple[str, str]],
        enums: dict[str, list[tuple[int, str]]] | None = None,
    ) -> Path:
        """Generate NiceGUI wrapper for a VI in the appropriate library folder."""
        module_name = self._to_module_name(vi_name)
        library_name = self._to_library_name(vi_name)
        ui_code = ContextBuilder.build_ui_wrapper(
            vi_name=vi_name,
            module_name=module_name,
            function_name=function_name,
            inputs=inputs,
            outputs=outputs,
            enums=enums or {},
        )

        # Write UI file next to the module in the same library folder
        if library_name:
            lib_dir = self.config.output_dir / library_name
            lib_dir.mkdir(parents=True, exist_ok=True)
            ui_path = lib_dir / f"{module_name}_ui.py"
        else:
            ui_path = self.config.output_dir / f"{module_name}_ui.py"

        ui_path.write_text(ui_code)
        return ui_path

    def _get_vilib_enums_by_type(
        self,
        vi_name: str,
        func_inputs: list[tuple[str, str]],
    ) -> dict[str, list[tuple[int, str]]]:
        """Get enum values for vilib VI by matching type annotations.

        Matches Python parameter type annotations to vilib enum names.
        E.g., if param type is 'SystemDirectoryType', looks up that enum.

        Args:
            vi_name: Name of the vilib VI
            func_inputs: List of (param_name, type_annotation) from AST

        Returns:
            Dict mapping parameter name -> list of (value, display_name) tuples
        """
        result: dict[str, list[tuple[int, str]]] = {}

        # Use enum resolver to look up enums by type annotation name
        enum_resolver = EnumResolver()

        # Match parameter type annotations to enum names
        for param_name, type_ann in func_inputs:
            # Try to resolve enum by name
            resolved = enum_resolver.resolve(name=type_ann)
            if resolved:
                values: list[tuple[int, str]] = []
                for idx, enum_val in resolved.values.items():
                    # Use description as display name, fallback to enum member name
                    display = enum_val.description or enum_val.name.replace("_", " ").title()
                    values.append((idx, display))

                # Sort by value
                values.sort(key=lambda x: x[0])
                result[param_name] = values

        return result

    def _to_var_name(self, name: str) -> str:
        """Convert a name to a valid Python variable name."""
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and result[0].isdigit():
            result = "_" + result
        return result

    def _extract_function_signature(
        self,
        code: str,
    ) -> tuple[str | None, list[tuple[str, str]], list[tuple[str, str]], dict[str, list[tuple[int, str]]]]:
        """Extract function signature from generated Python code.

        Parses the AST to find the main function and extract its
        name, parameters, return type, and any enum-like dict mappings.

        Args:
            code: Generated Python code

        Returns:
            Tuple of (function_name, inputs, outputs, enums) where:
            - function_name: Name of the function, or None if parsing failed
            - inputs: List of (name, type) for parameters
            - outputs: List of (name, type) for return values
            - enums: Dict mapping param name to list of (value, label) tuples
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None, [], [], {}

        # Find the first function definition (the main VI function)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                func_name = node.name
                inputs = self._extract_params(node)
                outputs = self._extract_returns(node, tree)
                # Look for dict-based enums in function body (fallback)
                enums = self._extract_enums(node, inputs)
                return func_name, inputs, outputs, enums

        return None, [], [], {}

    def _extract_enums(
        self,
        func: ast.FunctionDef,
        params: list[tuple[str, str]],
    ) -> dict[str, list[tuple[int, str]]]:
        """Extract enum-like dict mappings from function body.

        Looks for dict literals with integer keys that might represent
        enum values for function parameters.

        Args:
            func: Function AST node
            params: List of (name, type) for parameters

        Returns:
            Dict mapping parameter name to list of (value, label) tuples
        """
        enums: dict[str, list[tuple[int, str]]] = {}

        # Get parameter names that are int type (potential enums)
        int_params = {name for name, typ in params if typ in ("int", "int32", "integer")}
        if not int_params:
            return enums

        # Walk the function body looking for dict literals with int keys
        for node in ast.walk(func):
            if isinstance(node, ast.Dict):
                # Check if all keys are integer constants
                if not node.keys or not all(isinstance(k, ast.Constant) and isinstance(k.value, int) for k in node.keys if k is not None):
                    continue

                # Extract key-value pairs
                options: list[tuple[int, str]] = []
                for key, value in zip(node.keys, node.values):
                    if key is None:
                        continue
                    int_key = key.value  # type: ignore
                    # Try to get a meaningful label from the value
                    label = self._value_to_label(value, int_key)
                    options.append((int_key, label))

                if options:
                    # Sort by key value
                    options.sort(key=lambda x: x[0])
                    # Try to match this dict to a parameter
                    # For now, assign to the first int param we find
                    for param in int_params:
                        if param not in enums:
                            enums[param] = options
                            break

        return enums

    def _value_to_label(self, node: ast.expr, key: int) -> str:
        """Convert an AST value node to a human-readable label."""
        if isinstance(node, ast.Constant):
            return str(node.value)
        elif isinstance(node, ast.Call):
            # e.g., Path.home() -> "Home"
            if isinstance(node.func, ast.Attribute):
                return node.func.attr.replace("_", " ").title()
            elif isinstance(node.func, ast.Name):
                return node.func.id.replace("_", " ").title()
        elif isinstance(node, ast.BinOp):
            # e.g., Path.home() / 'Desktop' -> extract string part
            if isinstance(node.right, ast.Constant):
                return str(node.right.value)
        elif isinstance(node, ast.Subscript):
            # e.g., paths[0] -> use key
            pass
        # Fallback to key number
        return f"Option {key}"

    def _extract_params(self, func: ast.FunctionDef) -> list[tuple[str, str]]:
        """Extract parameter names and types from function definition."""
        params = []
        for arg in func.args.args:
            name = arg.arg
            if arg.annotation:
                type_str = ast.unparse(arg.annotation)
            else:
                type_str = "Any"
            params.append((name, type_str))
        return params

    def _extract_returns(
        self,
        func: ast.FunctionDef,
        tree: ast.Module | None = None,
    ) -> list[tuple[str, str]]:
        """Extract return type from function definition.

        For tuple/NamedTuple returns, creates multiple output entries.
        """
        if not func.returns:
            return []

        return_annotation = ast.unparse(func.returns)

        # Check for tuple return (multiple outputs)
        if return_annotation.startswith("tuple["):
            # Parse tuple elements
            # tuple[int, str, Path] -> [("result_0", "int"), ("result_1", "str"), ...]
            inner = return_annotation[6:-1]  # Remove "tuple[" and "]"
            # Simple split - handles basic cases
            types = [t.strip() for t in inner.split(",")]
            return [(f"result_{i}", t) for i, t in enumerate(types)]

        if return_annotation == "None":
            return []

        # Check if return type is a NamedTuple defined in the module
        if tree:
            namedtuple_fields = self._find_namedtuple_fields(tree, return_annotation)
            if namedtuple_fields:
                return namedtuple_fields

        # Single return value
        return [("result", return_annotation)]

    def _find_namedtuple_fields(
        self,
        tree: ast.Module,
        class_name: str,
    ) -> list[tuple[str, str]] | None:
        """Find fields of a NamedTuple class by name.

        Args:
            tree: AST module
            class_name: Name of the NamedTuple class

        Returns:
            List of (field_name, field_type) tuples, or None if not found
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                # Check if it inherits from NamedTuple
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "NamedTuple":
                        # Extract annotated fields
                        fields = []
                        for item in node.body:
                            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                                field_name = item.target.id
                                field_type = ast.unparse(item.annotation) if item.annotation else "Any"
                                fields.append((field_name, field_type))
                        return fields if fields else None
        return None

    def _generate_package_init(self, results: list[ConversionResult]) -> None:
        """Generate the main package __init__.py."""
        lines = [
            '"""Auto-generated LabVIEW conversion package."""',
            "",
            "from __future__ import annotations",
            "",
        ]

        # Import successful conversions - use relative imports with library paths
        exports = []
        for result in results:
            if result.success and result.output_path:
                module = result.output_path.stem

                # Get exported names and library from state
                converted = self.state.get_module(result.vi_name)
                if converted and converted.exports:
                    library = converted.library_name
                    for export in converted.exports:
                        # Use library path if applicable
                        if library:
                            lines.append(f"from .{library}.{module} import {export}")
                        else:
                            lines.append(f"from .{module} import {export}")
                        exports.append(export)

        lines.append("")
        lines.append(f"__all__ = {exports!r}")

        init_path = self.config.output_dir / "__init__.py"
        init_path.write_text("\n".join(lines))

    def _generate_ui_package_init(self, results: list[ConversionResult]) -> None:
        """Generate exports for UI wrappers in main __init__.py.

        Since UI files are now next to modules, we don't need a separate ui/ package.
        This is kept for backwards compatibility but could be removed.
        """
        # UI files are now in the main package, no separate ui/ init needed
        pass

    def _generate_ui_app(self, results: list[ConversionResult]) -> None:
        """Copy explorer.py to output directory as app.py."""
        ui_results = [r for r in results if r.success and r.ui_path]
        if not ui_results:
            return

        explorer_src = Path(__file__).parent.parent / "explorer.py"
        app_path = self.config.output_dir / "app.py"
        shutil.copy(explorer_src, app_path)
        print(f"  Copied app.py - run with: python {app_path}")

    def _generate_library_init(
        self,
        lib_dir: Path,
        results: list[ConversionResult],
    ) -> None:
        """Generate __init__.py for a library package."""
        lines = [
            '"""Library package."""',
            "",
        ]

        exports = []
        for result in results:
            if result.success and result.output_path:
                module = result.output_path.stem
                converted = self.state.get_module(result.vi_name)
                if converted and converted.exports:
                    for export in converted.exports:
                        # Use relative imports within the library package
                        lines.append(f"from .{module} import {export}")
                        exports.append(export)

        lines.append("")
        lines.append(f"__all__ = {exports!r}")

        (lib_dir / "__init__.py").write_text("\n".join(lines))

    def _generate_class_init(self, lvclass: "LVClass") -> list[str]:
        """Generate __init__ method for a class."""
        lines = ["    def __init__(self) -> None:"]

        if lvclass.private_data_ctl:
            lines.append("        # Private data from LabVIEW class")
            lines.append(f"        # Control: {lvclass.private_data_ctl}")
            lines.append("        self._data: dict = {}")
        else:
            lines.append("        pass")

        lines.append("")
        return lines

    def _convert_method(self, lvclass: "LVClass", method) -> list[str]:
        """Convert a single class method."""
        # Get VI context from graph - structured data for LLM
        vi_context = self.graph.get_vi_context(method.name)

        # Get types
        types = self.type_registry.get_types_for_vi(method.name)

        # Build context from structured graph data
        context = ContextBuilder.build_method_context(
            vi_context=vi_context,
            method_name=method.name,
            class_name=self._to_class_name(lvclass.name),
            visibility=method.scope,
            is_static=method.is_static,
            converted_deps={},
            shared_types=types,
        )

        # Generate method code
        code = generate_code(context, self.config.llm_config)
        code = self._extract_code(code)

        # Ensure proper indentation
        lines = []
        for line in code.split("\n"):
            if line.strip():
                # Add class-level indentation if not already present
                if not line.startswith("    "):
                    lines.append("    " + line)
                else:
                    lines.append(line)
            else:
                lines.append("")

        return lines

    def _extract_code(self, response: str) -> str:
        """Extract Python code from LLM response."""
        # Check for markdown code block
        if "```python" in response:
            start = response.find("```python") + 9
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        if "```" in response:
            start = response.find("```") + 3
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        # Assume entire response is code
        return response.strip()

    def _to_function_name(self, name: str) -> str:
        """Convert VI name to Python function name."""
        name = name.replace(".vi", "").replace(".VI", "")
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "vi_" + result
        return result or "vi_function"

    def _to_class_name(self, name: str) -> str:
        """Convert name to PascalCase class name."""
        name = name.replace(".lvclass", "").replace(".LVCLASS", "")
        words = name.replace("-", " ").replace("_", " ").split()
        return "".join(word.capitalize() for word in words) or "VIClass"

    def _parse_qualified_name(self, name: str) -> tuple[str | None, str]:
        """Parse qualified VI name into (library, vi_name).

        Args:
            name: Qualified name like "Library.lvlib:SubVI.vi" or just "SubVI.vi"

        Returns:
            Tuple of (library_name or None, vi_name)
        """
        # Handle qualified names like "Library.lvlib:SubVI.vi"
        if ":" in name:
            parts = name.split(":", 1)
            library = parts[0].replace(".lvlib", "").replace(".lvclass", "")
            vi_name = parts[1]
            return (library, vi_name)
        return (None, name)

    def _to_module_name(self, name: str) -> str:
        """Convert name to valid Python module name (without library prefix)."""
        # Extract just the VI name part
        _, vi_name = self._parse_qualified_name(name)
        vi_name = vi_name.replace(".vi", "").replace(".VI", "")
        vi_name = vi_name.replace(".lvclass", "").replace(".lvlib", "")
        result = vi_name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        return result or "module"

    def _to_library_name(self, name: str) -> str | None:
        """Extract library name from qualified VI name.

        Returns None if VI is not in a library.
        """
        # Check for Library.lvlib: prefix
        library, _ = self._parse_qualified_name(name)
        if library:
            result = library.lower().replace(" ", "_").replace("-", "_")
            result = "".join(c for c in result if c.isalnum() or c == "_")
            return result or None

        # Check resolver for category info (vilib, openg, etc.)
        vi_name = name if name.endswith(".vi") else f"{name}.vi"
        vi_info = self.vilib_resolver.resolve_by_name(vi_name)
        if vi_info and vi_info.category:
            # e.g., "openg/file" becomes the library path
            return vi_info.category

        # Fallback: __ogtk suffix → openg root
        if "__ogtk" in name:
            return "openg"

        return None

    def _get_module_path(self, vi_name: str) -> tuple[Path, str | None]:
        """Get the output path and library name for a VI.

        Args:
            vi_name: Qualified VI name

        Returns:
            Tuple of (output_path, library_name)
        """
        module_name = self._to_module_name(vi_name)
        library_name = self._to_library_name(vi_name)

        if library_name:
            # Create library directory
            lib_dir = self.config.output_dir / library_name
            lib_dir.mkdir(parents=True, exist_ok=True)
            return (lib_dir / f"{module_name}.py", library_name)
        else:
            # Root-level module
            return (self.config.output_dir / f"{module_name}.py", None)

    def _map_type(self, lv_type: str) -> str:
        """Map LabVIEW type to Python type."""
        type_map = {
            "stdString": "str",
            "stdNum": "float",
            "stdDBL": "float",
            "stdI32": "int",
            "stdBool": "bool",
            "stdPath": "Path",
        }
        return type_map.get(lv_type, "Any")
