"""Main conversion agent loop."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..llm import LLMConfig, generate_code
from .context import ContextBuilder, VISignature
from .primitives import PrimitiveRegistry
from .state import ConversionState, get_progress
from .types import SharedTypeRegistry
from .validator import CodeValidator, ErrorFormatter, ValidatorConfig

if TYPE_CHECKING:
    from ..graph import VIGraph
    from ..structure import LVClass, LVLibrary


@dataclass
class ConversionConfig:
    """Configuration for the conversion agent."""

    output_dir: Path
    max_retries: int = 3
    generate_ui: bool = False  # Generate NiceGUI wrappers
    llm_config: LLMConfig = field(default_factory=LLMConfig)

    # Validation settings
    validate_syntax: bool = True
    validate_imports: bool = True
    validate_types: bool = True  # Run mypy


@dataclass
class ConversionResult:
    """Result of converting a single VI."""

    vi_name: str
    python_code: str
    output_path: Path | None
    success: bool
    errors: list[str] = field(default_factory=list)
    attempts: int = 1
    ui_path: Path | None = None  # Path to UI wrapper if generated


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
        graph: VIGraph,
        config: ConversionConfig,
    ) -> None:
        self.graph = graph
        self.config = config
        self.state = ConversionState()
        self.type_registry = SharedTypeRegistry()
        self.primitive_registry = PrimitiveRegistry()

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
        # Ensure output directory exists
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        # Pre-analysis: discover types and primitives
        self._run_pre_analysis()

        # Get conversion order (leaves first)
        order = self.graph.get_conversion_order()
        results: list[ConversionResult] = []

        print(f"Converting {len(order)} VIs in dependency order...")

        for i, vi_name in enumerate(order, 1):
            print(f"[{i}/{len(order)}] Converting: {vi_name}")

            result = self.convert_vi(vi_name)
            results.append(result)

            if result.success:
                self.state.mark_converted(vi_name, result.output_path)
                print(f"  ✓ Success ({result.attempts} attempt(s))")
            else:
                self.state.mark_failed(vi_name)
                print(f"  ✗ Failed after {result.attempts} attempts")
                for error in result.errors[:3]:
                    print(f"    - {error}")

        # Generate package __init__.py
        self._generate_package_init(results)

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
        # Get VI context from graph
        vi_context = self.graph.get_vi_context(vi_name)

        # Get Cypher representation
        cypher = self.graph.get_vi_cypher(vi_name)

        # Extract inputs/outputs
        inputs = self._extract_io(vi_context.get("inputs", []))
        outputs = self._extract_io(vi_context.get("outputs", []))

        # Get converted SubVI signatures
        subvi_sigs = self._get_subvi_signatures(vi_context)

        # Get relevant types and primitives
        types = self.type_registry.get_types_for_vi(vi_name)
        primitive_names = self.primitive_registry.get_primitive_names(vi_name)

        # Build primitive mappings (primResID -> function name)
        primitive_mappings = self._get_primitive_mappings(vi_context)

        # Extract expected primitives and SubVIs for completeness checking
        expected_primitives = list(primitive_mappings.values())
        expected_subvis = [
            op["name"]
            for op in vi_context.get("operations", [])
            if "SubVI" in op.get("labels", []) and op.get("name")
        ]

        # Build initial context
        context = ContextBuilder.build_vi_context(
            cypher_graph=cypher,
            vi_name=vi_name,
            inputs=inputs,
            outputs=outputs,
            converted_deps=subvi_sigs,
            shared_types=types,
            primitives_available=primitive_names,
            primitive_mappings=primitive_mappings,
        )

        code = ""
        errors: list[str] = []
        original_context = context  # Preserve for error feedback

        for attempt in range(1, self.config.max_retries + 1):
            # Generate code
            code = generate_code(context, self.config.llm_config)
            code = self._extract_code(code)

            # Validate with completeness check
            validation = self.validator.validate(
                code, vi_name, expected_primitives, expected_subvis
            )

            if validation.is_valid:
                # Success - write to file
                output_path = self._write_vi_module(vi_name, code)

                # Optionally generate UI wrapper
                ui_path = None
                if self.config.generate_ui and inputs:
                    ui_path = self._generate_ui_wrapper(vi_name, inputs, outputs)

                return ConversionResult(
                    vi_name=vi_name,
                    python_code=code,
                    output_path=output_path,
                    success=True,
                    attempts=attempt,
                    ui_path=ui_path,
                )

            # Validation failed - build error context with original requirements
            errors = [e.message for e in validation.errors]
            context = ContextBuilder.build_error_context(
                code, validation.errors, original_context
            )

        # Max retries exceeded
        return ConversionResult(
            vi_name=vi_name,
            python_code=code,
            output_path=None,
            success=False,
            errors=errors,
            attempts=self.config.max_retries,
        )

    def convert_lvclass(self, lvclass: LVClass) -> ConversionResult:
        """Convert a LabVIEW class to a Python class.

        Args:
            lvclass: Parsed LVClass object

        Returns:
            ConversionResult for the class
        """
        class_name = self._to_class_name(lvclass.name)
        module_name = self._to_module_name(lvclass.name)

        lines = [
            f'"""Python class converted from {lvclass.name}."""',
            "",
            "from __future__ import annotations",
            "",
            "from dataclasses import dataclass, field",
            "from pathlib import Path",
            "from typing import Any",
            "",
        ]

        # Add type imports
        types = self.type_registry.get_types_for_vi(lvclass.name)
        if types:
            type_names = ", ".join(t.name for t in types)
            lines.append(f"from .types import {type_names}")
            lines.append("")

        # Generate class definition
        parent = ""
        if lvclass.parent_class:
            parent_name = self._to_class_name(lvclass.parent_class)
            parent = f"({parent_name})"

        lines.append(f"class {class_name}{parent}:")
        lines.append(f'    """Converted from LabVIEW class: {lvclass.name}."""')
        lines.append("")

        # Generate __init__ with private data
        lines.extend(self._generate_class_init(lvclass))

        # Convert each method
        for method in lvclass.methods:
            method_lines = self._convert_method(lvclass, method)
            lines.extend(method_lines)
            lines.append("")

        code = "\n".join(lines)

        # Validate
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

    def convert_lvlib(self, lvlib: LVLibrary) -> list[ConversionResult]:
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
        """Run pre-analysis pass to discover types and primitives."""
        print("Running pre-analysis...")

        # Discover from graph
        self.type_registry.discover_from_graph(self.graph)
        self.primitive_registry.discover_from_graph(self.graph)

        # Generate types.py files
        types_files = self.type_registry.generate_all_types_files(self.config.output_dir)
        if types_files:
            print(f"  Generated {len(types_files)} types.py file(s)")

        # Generate primitives package
        prims = self.primitive_registry.get_all_primitives()
        if prims:
            self.primitive_registry.generate_primitives_package(
                self.config.output_dir,
                self.config.llm_config,
            )
            print(f"  Generated primitives/ package ({len(prims)} primitives)")

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

    def _get_subvi_signatures(
        self,
        vi_context: dict,
    ) -> dict[str, VISignature]:
        """Get signatures for already-converted SubVIs."""
        signatures = {}

        for subvi in vi_context.get("subvi_calls", []):
            subvi_name = subvi.get("name", "")
            if self.state.is_converted(subvi_name):
                module = self.state.get_module(subvi_name)
                if module:
                    signatures[subvi_name] = VISignature(
                        name=subvi_name,
                        module_name=module.module_name,
                        function_name=module.exports[0] if module.exports else "",
                        signature=module.signature,
                        import_statement=self.state.get_import_statement(subvi_name),
                    )

        return signatures

    def _get_primitive_mappings(self, vi_context: dict) -> dict[int, str]:
        """Get primResID -> function name mappings for a VI.

        Looks up known primitives from the operations in the VI context.
        """
        from .primitives import KNOWN_PRIMITIVES

        mappings = {}
        operations = vi_context.get("operations", [])

        for op in operations:
            if "Primitive" in op.get("labels", []):
                prim_id = op.get("primResID")
                if prim_id is not None and prim_id in KNOWN_PRIMITIVES:
                    func_name, _ = KNOWN_PRIMITIVES[prim_id]
                    mappings[prim_id] = func_name

        return mappings

    def _write_vi_module(self, vi_name: str, code: str) -> Path:
        """Write VI code to a module file."""
        module_name = self._to_module_name(vi_name)
        output_path = self.config.output_dir / f"{module_name}.py"
        output_path.write_text(code)
        return output_path

    def _generate_ui_wrapper(
        self,
        vi_name: str,
        inputs: list[tuple[str, str]],
        outputs: list[tuple[str, str]],
    ) -> Path:
        """Generate NiceGUI wrapper for a VI."""
        function_name = self._to_function_name(vi_name)
        ui_code = ContextBuilder.build_ui_wrapper(
            vi_name=vi_name,
            function_name=function_name,
            inputs=inputs,
            outputs=outputs,
        )

        # Ensure ui/ directory exists
        ui_dir = self.config.output_dir / "ui"
        ui_dir.mkdir(parents=True, exist_ok=True)

        module_name = self._to_module_name(vi_name)
        ui_path = ui_dir / f"{module_name}_ui.py"
        ui_path.write_text(ui_code)

        return ui_path

    def _generate_package_init(self, results: list[ConversionResult]) -> None:
        """Generate the main package __init__.py."""
        lines = [
            '"""Auto-generated LabVIEW conversion package."""',
            "",
            "from __future__ import annotations",
            "",
        ]

        # Import successful conversions
        exports = []
        for result in results:
            if result.success and result.output_path:
                module = result.output_path.stem

                # Get exported names from state
                converted = self.state.get_module(result.vi_name)
                if converted and converted.exports:
                    for export in converted.exports:
                        lines.append(f"from .{module} import {export}")
                        exports.append(export)

        lines.append("")
        lines.append(f"__all__ = {exports!r}")

        init_path = self.config.output_dir / "__init__.py"
        init_path.write_text("\n".join(lines))

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
                        lines.append(f"from .{module} import {export}")
                        exports.append(export)

        lines.append("")
        lines.append(f"__all__ = {exports!r}")

        (lib_dir / "__init__.py").write_text("\n".join(lines))

    def _generate_class_init(self, lvclass: LVClass) -> list[str]:
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

    def _convert_method(self, lvclass: LVClass, method) -> list[str]:
        """Convert a single class method."""
        # Get VI context from graph
        vi_context = self.graph.get_vi_context(method.name)
        cypher = self.graph.get_vi_cypher(method.name)

        # Get types
        types = self.type_registry.get_types_for_vi(method.name)

        # Build context
        context = ContextBuilder.build_method_context(
            cypher_graph=cypher,
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

    def _to_module_name(self, name: str) -> str:
        """Convert name to valid Python module name."""
        name = name.replace(".vi", "").replace(".VI", "")
        name = name.replace(".lvclass", "").replace(".lvlib", "")
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        return result or "module"

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
