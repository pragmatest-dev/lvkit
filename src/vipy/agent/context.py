"""Context building for LLM prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state import ConvertedModule
    from .types import SharedType
    from .validator import ValidationError


@dataclass
class VISignature:
    """Signature info for a VI (for SubVI imports)."""

    name: str
    module_name: str
    function_name: str
    signature: str  # e.g., "def calculate(a: float) -> float"
    import_statement: str  # e.g., "from .calculate import calculate"


class ContextBuilder:
    """Builds LLM prompts for VI conversion.

    Constructs context with:
    - VI graph (inputs, outputs, operations, data flow)
    - Already-converted SubVI signatures
    - Relevant shared types
    - Primitive imports available
    """

    # Standard function template - each VI becomes a callable function
    FUNCTION_TEMPLATE = '''Convert this LabVIEW VI to a Python function.

## VI Graph (Cypher representation)
{cypher_graph}

## Primitive Reference
The following primResID values map to these functions from `.primitives`:
{primitive_mappings}

## Available Imports
{available_imports}

Standard library imports you may need:
- `from pathlib import Path` for path operations
- `import os` for environment variables and OS operations

## Shared Types
{shared_types}

## Requirements
- Create a function named `{function_name}`
- Parameters from VI inputs: {inputs}
- Return values from VI outputs: {outputs}
- For each `:Primitive` node, use the corresponding function from `.primitives` based on its primResID
- Follow the data flow: Input nodes → operations → Output nodes
- Include ALL necessary imports at the top of the code
- Add proper type hints to all parameters and return value

Output ONLY the Python code (with imports), no explanations.'''

    # Method template - for lvclass methods
    METHOD_TEMPLATE = '''Convert this LabVIEW class method to a Python method.

## VI Graph (Cypher representation)
{cypher_graph}

## Class Context
This method belongs to class `{class_name}`.
Method visibility: {visibility}
{static_decorator}

## Available Imports
{available_imports}

## Requirements
- Method name: `{method_name}`
- {"@staticmethod - no self parameter" if is_static else "Instance method - use self for instance data"}
- Access class data via self._data attributes
- Call other class methods via self.method_name()
- External SubVI calls should be imported functions

Output ONLY the Python method code (with proper indentation), no explanations.'''

    # UI wrapper template - consistent NiceGUI pattern for each VI
    UI_WRAPPER_TEMPLATE = '''"""NiceGUI wrapper for {vi_name}."""

from nicegui import ui

from .{module_name} import {function_name}


class {class_name}UI:
    """UI wrapper for {vi_name}.

    Provides NiceGUI interface with:
    - Input widgets for each control
    - Execute button
    - Output display for indicators
    """

    def __init__(self) -> None:
        # Input state
{input_attrs}

        # Output state
{output_attrs}

    def build(self) -> None:
        """Build the UI components."""
        with ui.card().classes("p-4"):
            ui.label("{vi_name}").classes("text-lg font-bold")

            # Inputs
            with ui.column().classes("gap-2"):
{input_widgets}

            # Execute button
            ui.button("Run", on_click=self._execute).classes("mt-4")

            # Outputs
            with ui.column().classes("gap-2 mt-4"):
                ui.label("Results").classes("font-medium")
{output_widgets}

    async def _execute(self) -> None:
        """Execute the VI logic and update outputs."""
        try:
            result = {function_call}
            {result_assignment}
        except Exception as e:
            ui.notify(f"Error: {{e}}", type="negative")


def create() -> {class_name}UI:
    """Factory function to create and build UI."""
    wrapper = {class_name}UI()
    wrapper.build()
    return wrapper
'''

    @staticmethod
    def build_vi_context(
        cypher_graph: str,
        vi_name: str,
        inputs: list[tuple[str, str]],  # [(name, type), ...]
        outputs: list[tuple[str, str]],
        converted_deps: dict[str, VISignature],
        shared_types: list[SharedType],
        primitives_available: list[str],
        primitive_mappings: dict[int, str] | None = None,
    ) -> str:
        """Build context for converting a standalone VI.

        Args:
            cypher_graph: Cypher representation of the VI
            vi_name: Name of the VI
            inputs: List of (name, type) tuples for inputs
            outputs: List of (name, type) tuples for outputs
            converted_deps: Already-converted SubVIs
            shared_types: Relevant shared types
            primitives_available: List of available primitive function names
            primitive_mappings: Map of primResID -> function name

        Returns:
            Complete prompt for LLM
        """
        # Build primitive mappings section
        if primitive_mappings:
            mapping_lines = []
            for prim_id, func_name in sorted(primitive_mappings.items()):
                mapping_lines.append(f"- primResID {prim_id} → {func_name}()")
            primitive_section = "\n".join(mapping_lines)
        else:
            primitive_section = "No known primitives in this VI"

        # Build available imports section
        import_lines = []

        # Add SubVI imports
        for sig in converted_deps.values():
            import_lines.append(f"# {sig.signature}")
            import_lines.append(sig.import_statement)

        # Add primitive imports
        if primitives_available:
            prims = ", ".join(primitives_available)
            import_lines.append(f"from .primitives import {prims}")

        # Add type imports
        if shared_types:
            type_names = ", ".join(t.name for t in shared_types)
            import_lines.append(f"from .types import {type_names}")

        available_imports = "\n".join(import_lines) if import_lines else "# No special imports needed"

        # Build shared types section
        types_section = ContextBuilder._format_shared_types(shared_types)

        # Format inputs/outputs
        inputs_str = ", ".join(f"{name}: {typ}" for name, typ in inputs) if inputs else "none"
        outputs_str = ", ".join(f"{name}: {typ}" for name, typ in outputs) if outputs else "none"

        # Generate function name
        function_name = ContextBuilder._to_function_name(vi_name)

        return ContextBuilder.FUNCTION_TEMPLATE.format(
            cypher_graph=cypher_graph,
            primitive_mappings=primitive_section,
            available_imports=available_imports,
            shared_types=types_section,
            function_name=function_name,
            inputs=inputs_str,
            outputs=outputs_str,
        )

    @staticmethod
    def build_method_context(
        cypher_graph: str,
        method_name: str,
        class_name: str,
        visibility: str,  # "public", "private", "protected"
        is_static: bool,
        converted_deps: dict[str, VISignature],
        shared_types: list[SharedType],
    ) -> str:
        """Build context for converting a class method.

        Args:
            cypher_graph: Cypher representation of the method VI
            method_name: Name of the method
            class_name: Name of the containing class
            visibility: Method visibility
            is_static: Whether method is static
            converted_deps: Already-converted SubVIs
            shared_types: Relevant shared types

        Returns:
            Complete prompt for LLM
        """
        # Build imports section
        import_lines = []
        for sig in converted_deps.values():
            import_lines.append(sig.import_statement)

        if shared_types:
            type_names = ", ".join(t.name for t in shared_types)
            import_lines.append(f"from .types import {type_names}")

        available_imports = "\n".join(import_lines) if import_lines else "# No special imports"

        # Determine method name with visibility prefix
        prefix = ""
        if visibility == "private":
            prefix = "_"
        elif visibility == "protected":
            prefix = "__"

        py_method_name = prefix + ContextBuilder._to_function_name(method_name)
        static_decorator = "@staticmethod" if is_static else ""

        return ContextBuilder.METHOD_TEMPLATE.format(
            cypher_graph=cypher_graph,
            class_name=class_name,
            visibility=visibility,
            static_decorator=static_decorator,
            method_name=py_method_name,
            is_static=is_static,
            available_imports=available_imports,
        )

    @staticmethod
    def build_error_context(
        code: str,
        errors: list[ValidationError],
    ) -> str:
        """Build context for error correction.

        Args:
            code: The broken Python code
            errors: List of validation errors

        Returns:
            Prompt for LLM to fix errors
        """
        from .validator import ErrorFormatter

        error_text = ErrorFormatter.format(errors)

        return f"""The following Python code has errors:

```python
{code}
```

{error_text}

Please fix these errors and return the corrected code.
Keep the same function signature and logic.
Output ONLY the corrected Python code, no explanations."""

    @staticmethod
    def build_ui_wrapper(
        vi_name: str,
        function_name: str,
        inputs: list[tuple[str, str]],  # [(name, type), ...]
        outputs: list[tuple[str, str]],
    ) -> str:
        """Generate NiceGUI wrapper for a VI.

        This creates a consistent UI pattern:
        - Input widgets bound to state
        - Execute button
        - Output display widgets

        Args:
            vi_name: Original VI name
            function_name: Python function name
            inputs: List of (name, type) tuples
            outputs: List of (name, type) tuples

        Returns:
            Complete UI wrapper Python code
        """
        module_name = ContextBuilder._to_function_name(vi_name)
        class_name = ContextBuilder._to_class_name(vi_name)

        # Generate input attributes
        input_attrs = []
        for name, typ in inputs:
            py_name = ContextBuilder._to_var_name(name)
            default = ContextBuilder._get_default(typ)
            input_attrs.append(f"        self.{py_name} = {default}")

        # Generate output attributes
        output_attrs = []
        for name, typ in outputs:
            py_name = ContextBuilder._to_var_name(name)
            default = ContextBuilder._get_default(typ)
            output_attrs.append(f"        self.{py_name} = {default}")

        # Generate input widgets
        input_widgets = []
        for name, typ in inputs:
            py_name = ContextBuilder._to_var_name(name)
            widget = ContextBuilder._get_input_widget(name, typ, py_name)
            input_widgets.append(f"                {widget}")

        # Generate output widgets
        output_widgets = []
        for name, typ in outputs:
            py_name = ContextBuilder._to_var_name(name)
            widget = ContextBuilder._get_output_widget(name, typ, py_name)
            output_widgets.append(f"                {widget}")

        # Generate function call
        args = ", ".join(f"self.{ContextBuilder._to_var_name(n)}" for n, _ in inputs)
        function_call = f"{function_name}({args})"

        # Generate result assignment
        if len(outputs) == 0:
            result_assignment = "pass  # No outputs"
        elif len(outputs) == 1:
            py_name = ContextBuilder._to_var_name(outputs[0][0])
            result_assignment = f"self.{py_name} = result"
        else:
            assignments = []
            for i, (name, _) in enumerate(outputs):
                py_name = ContextBuilder._to_var_name(name)
                assignments.append(f"self.{py_name} = result[{i}]")
            result_assignment = "\n            ".join(assignments)

        return ContextBuilder.UI_WRAPPER_TEMPLATE.format(
            vi_name=vi_name,
            module_name=module_name,
            function_name=function_name,
            class_name=class_name,
            input_attrs="\n".join(input_attrs) if input_attrs else "        pass",
            output_attrs="\n".join(output_attrs) if output_attrs else "        pass",
            input_widgets="\n".join(input_widgets) if input_widgets else "                pass",
            output_widgets="\n".join(output_widgets) if output_widgets else "                ui.label('No outputs')",
            function_call=function_call,
            result_assignment=result_assignment,
        )

    @staticmethod
    def _format_shared_types(types: list[SharedType]) -> str:
        """Format shared types for context."""
        if not types:
            return "# No shared types needed"

        lines = ["```python", "# Available shared types (from .types)"]
        for t in types:
            lines.append(f"@dataclass")
            lines.append(f"class {t.name}:")
            for field_name, field_type in t.fields:
                lines.append(f"    {field_name}: {field_type}")
            lines.append("")
        lines.append("```")
        return "\n".join(lines)

    @staticmethod
    def _to_function_name(name: str) -> str:
        """Convert VI name to Python function name."""
        # Remove extension
        name = name.replace(".vi", "").replace(".VI", "")
        # Replace spaces and dashes with underscores
        result = name.lower().replace(" ", "_").replace("-", "_")
        # Remove invalid characters
        result = "".join(c for c in result if c.isalnum() or c == "_")
        # Ensure starts with letter
        if result and not result[0].isalpha():
            result = "vi_" + result
        return result or "vi_function"

    @staticmethod
    def _to_class_name(name: str) -> str:
        """Convert VI name to Python class name (PascalCase)."""
        name = name.replace(".vi", "").replace(".VI", "")
        words = name.replace("-", " ").replace("_", " ").split()
        return "".join(word.capitalize() for word in words) or "VIClass"

    @staticmethod
    def _to_var_name(name: str) -> str:
        """Convert control/indicator name to Python variable name."""
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        if result and not result[0].isalpha():
            result = "var_" + result
        return result or "value"

    @staticmethod
    def _get_default(type_str: str) -> str:
        """Get default value for a type."""
        type_lower = type_str.lower()
        if "int" in type_lower or "i32" in type_lower or "i16" in type_lower:
            return "0"
        if "float" in type_lower or "dbl" in type_lower or "num" in type_lower:
            return "0.0"
        if "bool" in type_lower:
            return "False"
        if "str" in type_lower:
            return "''"
        if "path" in type_lower:
            return "Path('.')"
        if "list" in type_lower or "array" in type_lower:
            return "[]"
        return "None"

    @staticmethod
    def _get_input_widget(label: str, type_str: str, var_name: str) -> str:
        """Get NiceGUI input widget for a type."""
        type_lower = type_str.lower()
        if "bool" in type_lower:
            return f"ui.switch('{label}').bind_value(self, '{var_name}')"
        if "int" in type_lower or "float" in type_lower or "num" in type_lower or "dbl" in type_lower:
            return f"ui.number('{label}').bind_value(self, '{var_name}')"
        if "path" in type_lower:
            return f"ui.input('{label}').bind_value(self, '{var_name}').classes('w-full')"
        # Default to text input
        return f"ui.input('{label}').bind_value(self, '{var_name}')"

    @staticmethod
    def _get_output_widget(label: str, type_str: str, var_name: str) -> str:
        """Get NiceGUI output widget for a type."""
        type_lower = type_str.lower()
        if "bool" in type_lower:
            return f"ui.switch('{label}', value=False).bind_value_from(self, '{var_name}').props('disable')"
        if "int" in type_lower or "float" in type_lower or "num" in type_lower:
            return f"ui.number('{label}', value=0).bind_value_from(self, '{var_name}').props('readonly')"
        # Default to label display
        return f"ui.label().bind_text_from(self, '{var_name}', backward=lambda x: f'{label}: {{x}}')"
