"""Context building for LLM prompts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    # Pass JSON directly from graph database - LLMs understand structured data well
    FUNCTION_TEMPLATE = '''Convert this LabVIEW VI to a Python function.

## VI Context (from graph database)
```json
{vi_context_json}
```

## Key Constants - IMPORTANT
{key_constants}

## Available Imports
{available_imports}

## Shared Types
{shared_types}

## Requirements
- Function name: `{function_name}`
- MUST include `from __future__ import annotations` at the top
- MUST include `from typing import Any` if you use Any in type annotations
- MUST include type annotations on ALL parameters and return type
- Each VI input becomes a NAMED function parameter (e.g., `def func(path: str, count: int)`)
- Do NOT wrap inputs in a dict - use individual named parameters
- Use `data_flow` to understand execution order and wire connections (source -> destination)
- Use the Key Constants section above - it shows the Python equivalent for each constant

## Return Values (CRITICAL - USE NAMEDTUPLE)
Define a NamedTuple class for outputs, then return an instance:
```python
from typing import NamedTuple

class MyFuncResult(NamedTuple):
    settings_path: Path
    error_out: dict

def my_func(input1: str) -> MyFuncResult:
    \"\"\"Description of what this VI does.

    :param input1: "Original Input Name" - description
    :return: ("Settings Path", "error out")
    \"\"\"
    return MyFuncResult(settings_path=path, error_out=err)
```

Rules:
- NamedTuple class name: CamelCase function name + "Result" (e.g., `GetSettingsPathResult`)
- Field names from Outputs section, converted to snake_case
- For cluster outputs (like `error out: Cluster (status, code, source)`), use dict: `{{"status": False, "code": 0, "source": ""}}`
- ALWAYS use keyword arguments in the return: `return Result(field1=val1, field2=val2)`
- Docstring MUST include `:param name: "Original Name"` for each input
- Docstring MUST include `:return: ("Original Name 1", "Original Name 2")` listing original output names

## Primitives (IMPORTANT)
Operations with "Primitive" label are LabVIEW built-in operations. Use them INLINE:
- `python_hint`: The Python equivalent - USE THIS DIRECTLY in your code
- `primitive_name`: The official LabVIEW primitive name
- `terminal_names`: Input/output terminal names for parameter mapping

Example: If python_hint is "array[index]", write `result = my_array[i]` directly.
DO NOT call wrapper functions for simple operations - use the Python equivalent inline.

## SubVIs - IMPORTANT
Operations with "SubVI" label are ALREADY CONVERTED Python functions.
Each SubVI operation in the JSON has:
- `python_function`: The exact function name to call
- `python_signature`: The function signature showing parameters

YOU MUST:
1. Import them using the imports shown in "Available Imports" above
2. Call them in your code where the data flow indicates
3. DO NOT raise NotImplementedError for SubVIs - they are already implemented!

## Enums
{enum_context}

Output ONLY the Python code (with imports), no explanations.'''

    # Method template - for lvclass methods
    # Pass JSON directly from graph database
    METHOD_TEMPLATE = '''Convert this LabVIEW class method to a Python method.

## Method: `{class_name}.{method_name}`
Visibility: {visibility}
{static_decorator}

## VI Context (from graph database)
```json
{vi_context_json}
```

## Available Imports
{available_imports}

## Requirements
- Create a single method `{method_name}` - NO nested function definitions
- Call operations inline (e.g., `result = self._helper(value)` or `result = some_primitive(value)`)
- Access class data via self._data attributes
- Call other class methods via self.method_name()
- External SubVI calls should be imported functions

Output ONLY the Python method code (with proper indentation), no explanations.'''

    # UI wrapper template - responsive layout: inputs left, outputs right on desktop
    UI_WRAPPER_TEMPLATE = '''"""NiceGUI wrapper for {vi_name}."""

from pathlib import Path

from nicegui import ui

from .{module_name} import {function_name}


class {class_name}UI:
    """UI wrapper for {vi_name}.

    Provides NiceGUI interface with:
    - Run button at top with title
    - Input widgets on left (top on mobile)
    - Output display on right (bottom on mobile)
    """

    def __init__(self) -> None:
        # Input state
{input_attrs}

        # Output state
{output_attrs}

    def build(self) -> None:
        """Build the UI components."""
        with ui.card().classes("p-4 w-full max-w-4xl"):
            # Header with title and run button
            with ui.row().classes("w-full items-center gap-4 mb-4"):
                ui.button("Run", on_click=self._execute)
                ui.label("{vi_name}").classes("text-lg font-bold")

            # Responsive row: horizontal on md+, vertical on mobile
            with ui.element("div").classes("flex flex-col md:flex-row gap-4 w-full"):
                # Inputs panel (left side)
                with ui.column().classes("flex-1 gap-2"):
                    ui.label("Inputs").classes("font-medium text-gray-600")
{input_widgets}

                # Outputs panel (right side)
                with ui.column().classes("flex-1 gap-2"):
                    ui.label("Outputs").classes("font-medium text-gray-600")
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
        vi_context: dict,
        vi_name: str,
        converted_deps: dict[str, VISignature],
        shared_types: list[SharedType],
        primitives_available: list[str],
        primitive_mappings: dict[int, str] | None = None,
        primitive_context: dict[int, dict] | None = None,
        enum_context: dict[str, dict] | None = None,
        from_library: str | None = None,
    ) -> str:
        """Build context for converting a standalone VI.

        Pass the graph database JSON directly - LLMs understand structured data well.
        This is simpler and more reliable than reformatting into prose.

        Args:
            vi_context: Structured VI data from graph.get_vi_context()
            vi_name: Name of the VI
            converted_deps: Already-converted SubVIs
            shared_types: Relevant shared types
            primitives_available: List of available primitive function names
            primitive_mappings: Mapping of primResID -> generated function name
            primitive_context: Rich primitive context with Python hints and terminals
            enum_context: Rich enum context with values and Python hints
            from_library: Library the VI belongs to (for relative imports)

        Returns:
            Complete prompt for LLM
        """
        # Clean the context - filter Void terminals, add SubVI and primitive info
        cleaned = ContextBuilder._clean_vi_context(
            vi_context, converted_deps, primitive_mappings, primitive_context
        )

        # Build available imports section
        import_lines = []

        # Add SubVI imports (already library-relative from converted_deps)
        for sig in converted_deps.values():
            import_lines.append(f"# {sig.signature}")
            import_lines.append(sig.import_statement)

        # Add primitive imports (relative depth depends on library)
        if primitives_available:
            prims = ", ".join(primitives_available)
            prefix = ".." if from_library else "."
            import_lines.append(f"from {prefix}primitives import {prims}")

        # Add type imports (relative depth depends on library)
        if shared_types:
            type_names = ", ".join(t.name for t in shared_types)
            prefix = ".." if from_library else "."
            import_lines.append(f"from {prefix}types import {type_names}")

        # Always add common imports (harmless if unused)
        import_lines.append("from pathlib import Path  # Use for file paths")
        import_lines.append("from typing import Any  # Use if needed for type annotations")

        available_imports = "\n".join(import_lines) if import_lines else "# No special imports needed"

        # Build shared types section
        types_section = ContextBuilder._format_shared_types(shared_types)

        # Generate function name
        function_name = ContextBuilder._to_function_name(vi_name)

        # Format enum context
        enum_section = ContextBuilder._format_enum_context(enum_context)

        # Format key constants prominently
        key_constants = ContextBuilder._format_key_constants(
            vi_context.get("constants", [])
        )

        return ContextBuilder.FUNCTION_TEMPLATE.format(
            function_name=function_name,
            vi_context_json=json.dumps(cleaned, indent=2),
            available_imports=available_imports,
            shared_types=types_section,
            enum_context=enum_section,
            key_constants=key_constants,
        )

    @staticmethod
    def _clean_vi_context(
        ctx: dict,
        converted_deps: dict[str, VISignature] | None = None,
        primitive_mappings: dict[int, str] | None = None,
        primitive_context: dict[int, dict] | None = None,
    ) -> dict:
        """Clean VI context for LLM consumption.

        - Filter Void terminals (unwired) from operations
        - Add SubVI function signatures for reference
        - Add primitive function names and Python hints for reference

        Args:
            ctx: Raw context from graph.get_vi_context()
            converted_deps: Already-converted SubVIs with their signatures
            primitive_mappings: Mapping of primResID -> generated function name
            primitive_context: Rich primitive context with hints and terminals

        Returns:
            Cleaned context dict
        """
        import copy
        cleaned = copy.deepcopy(ctx)
        primitive_mappings = primitive_mappings or {}
        primitive_context = primitive_context or {}

        # Filter Void terminals from operations
        if "operations" in cleaned:
            for op in cleaned["operations"]:
                if "terminals" in op:
                    op["terminals"] = [
                        t for t in op["terminals"]
                        if t.get("type") != "Void"
                    ]

        # Add SubVI signatures to operations for reference
        if converted_deps and "operations" in cleaned:
            for op in cleaned["operations"]:
                if "SubVI" in op.get("labels", []):
                    vi_name = op.get("name", "")
                    if vi_name in converted_deps:
                        sig = converted_deps[vi_name]
                        op["python_function"] = sig.function_name
                        op["python_signature"] = sig.signature

        # Add primitive function names and hints to operations
        if "operations" in cleaned:
            for op in cleaned["operations"]:
                if "Primitive" in op.get("labels", []):
                    prim_id = op.get("primResID")
                    if prim_id:
                        # Use rich context if available
                        if prim_id in primitive_context:
                            pctx = primitive_context[prim_id]
                            op["python_function"] = pctx.get("python_function", "")
                            op["primitive_name"] = pctx.get("name", "")
                            if pctx.get("python_hint"):
                                op["python_hint"] = pctx["python_hint"]
                            if pctx.get("terminals"):
                                op["terminal_names"] = [
                                    {"name": t.get("name"), "direction": t.get("direction")}
                                    for t in pctx["terminals"]
                                ]
                        # Fall back to simple mapping
                        elif prim_id in primitive_mappings:
                            op["python_function"] = primitive_mappings[prim_id]

        return cleaned

    @staticmethod
    def _format_inputs(inputs: list[dict]) -> str:
        """Format inputs from vi_context into readable text."""
        if not inputs:
            return "  (none)"
        lines = []
        for inp in inputs:
            name = inp.get("name", "unknown")
            typ = inp.get("type", "Any")
            labels = inp.get("labels", [])
            label_str = ", ".join(l for l in labels if l not in ("Input", "Output"))
            children = inp.get("children", [])
            if children:
                child_info = ", ".join(f"{c['name']}: {c.get('type', 'Any')}" for c in children)
                lines.append(f"  - {name}: {label_str} ({child_info})")
            else:
                lines.append(f"  - {name}: {label_str or typ}")
        return "\n".join(lines)

    @staticmethod
    def _format_outputs(outputs: list[dict]) -> str:
        """Format outputs from vi_context into readable text."""
        if not outputs:
            return "  (none)"
        lines = []
        for out in outputs:
            name = out.get("name", "unknown")
            typ = out.get("type", "Any")
            labels = out.get("labels", [])
            label_str = ", ".join(l for l in labels if l not in ("Input", "Output"))
            children = out.get("children", [])
            if children:
                child_info = ", ".join(f"{c['name']}: {c.get('type', 'Any')}" for c in children)
                lines.append(f"  - {name}: {label_str} ({child_info})")
            else:
                lines.append(f"  - {name}: {label_str or typ}")
        return "\n".join(lines)

    @staticmethod
    def _format_constants(constants: list[dict]) -> str:
        """Format constants from vi_context into readable text."""
        if not constants:
            return "  (none)"
        lines = []
        for const in constants:
            cid = const.get("id", "").split(":")[-1]  # Get just the UID
            value = const.get("python") or const.get("value", "?")
            typ = const.get("type", "")
            lines.append(f"  - c_{cid}: {value} ({typ})" if typ else f"  - c_{cid}: {value}")
        return "\n".join(lines)

    @staticmethod
    def _format_operations(
        operations: list[dict],
        primitive_mappings: dict[int, str] | None = None,
    ) -> str:
        """Format operations from vi_context as callable methods.

        Filters out Void terminals (unwired) to show only meaningful connections.

        Args:
            operations: List of operation dictionaries from vi_context
            primitive_mappings: Optional mapping of primResID -> generated function name
        """
        if not operations:
            return "  (none)"

        primitive_mappings = primitive_mappings or {}
        lines = []

        for op in operations:
            labels = op.get("labels", [])
            name = op.get("name")
            prim_id = op.get("primResID")
            terminals = op.get("terminals", [])

            # Filter out Void terminals (unwired connections)
            inputs = [t for t in terminals
                      if t.get("direction") == "input" and t.get("type") != "Void"]
            outputs = [t for t in terminals
                       if t.get("direction") == "output" and t.get("type") != "Void"]

            # Sort by index and get types
            in_types = [t.get("type", "Any") for t in sorted(inputs, key=lambda x: x.get("index", 0))]
            out_types = [t.get("type", "Any") for t in sorted(outputs, key=lambda x: x.get("index", 0))]

            # Format return type
            if len(out_types) == 0:
                ret_type = "None"
            elif len(out_types) == 1:
                ret_type = out_types[0]
            else:
                ret_type = f"tuple[{', '.join(out_types)}]"

            # Build callable description based on type
            if "SubVI" in labels and name:
                # SubVI: show as imported function
                func_name = ContextBuilder._to_function_name(name)
                sig = f"({', '.join(in_types)}) -> {ret_type}"
                lines.append(f"  - {func_name}{sig}  # SubVI: {name}")
            elif "Primitive" in labels and prim_id is not None:
                # Use generated name if available, otherwise fall back to primitive_ID
                func_name = primitive_mappings.get(prim_id, f"primitive_{prim_id}")
                sig = f"({', '.join(in_types)}) -> {ret_type}"
                lines.append(f"  - {func_name}{sig}")
            elif "Conditional" in labels:
                cond_type = op.get("type", "select")
                lines.append(f"  - if/match structure ({cond_type})")
            elif "Loop" in labels:
                loop_type = op.get("type", "for")
                lines.append(f"  - {loop_type} loop")
            else:
                lines.append(f"  - {', '.join(labels)}")
        return "\n".join(lines)

    @staticmethod
    def _format_data_flow(
        data_flow: list[dict],
        constants: list[dict] | None = None,
        primitive_mappings: dict[int, str] | None = None,
    ) -> str:
        """Format data flow as sequential assignment statements.

        Args:
            data_flow: List of flow dictionaries from vi_context
            constants: List of constant dictionaries for value lookup
            primitive_mappings: Mapping of primResID -> generated function name
        """
        if not data_flow:
            return "  (no connections)"

        primitive_mappings = primitive_mappings or {}

        # Build constant value lookup by ID
        const_values: dict[str, str] = {}
        if constants:
            for c in constants:
                cid = c.get("id", "")
                # Prefer python hint, then value
                value = c.get("python") or c.get("value") or "?"
                const_values[cid] = value

        lines = []
        for flow in data_flow:
            # Source description
            from_name = flow.get("from_parent_name")
            from_id = flow.get("from_parent_id", "")
            from_labels = flow.get("from_parent_labels", [])
            from_idx = flow.get("from_index", 0)
            from_prim = flow.get("from_prim")

            if "Constant" in from_labels:
                # Look up actual constant value
                value = const_values.get(from_id, "?")
                # Truncate long values
                if len(value) > 50:
                    value = value[:47] + "..."
                from_desc = f'"{value}"'
            elif "Control" in from_labels or "Input" in from_labels:
                from_desc = ContextBuilder._to_var_name(from_name) if from_name else "input"
            elif "SubVI" in from_labels and from_name:
                func_name = ContextBuilder._to_function_name(from_name)
                from_desc = f"{func_name}()[{from_idx}]" if from_idx > 0 else f"{func_name}()"
            elif "Primitive" in from_labels and from_prim is not None:
                # Use generated name if available
                func_name = primitive_mappings.get(from_prim, f"primitive_{from_prim}")
                from_desc = f"{func_name}()[{from_idx}]" if from_idx > 0 else f"{func_name}()"
            else:
                from_desc = from_name or "?"

            # Destination description
            to_name = flow.get("to_parent_name")
            to_labels = flow.get("to_parent_labels", [])
            to_idx = flow.get("to_index", 0)
            to_prim = flow.get("to_prim")

            if "Indicator" in to_labels or "Output" in to_labels:
                to_desc = ContextBuilder._to_var_name(to_name) if to_name else "output"
            elif "SubVI" in to_labels and to_name:
                func_name = ContextBuilder._to_function_name(to_name)
                to_desc = f"{func_name}.input[{to_idx}]"
            elif "Primitive" in to_labels and to_prim is not None:
                # Use generated name if available
                func_name = primitive_mappings.get(to_prim, f"primitive_{to_prim}")
                to_desc = f"{func_name}.input[{to_idx}]"
            else:
                to_desc = to_name or "?"

            lines.append(f"  {from_desc} -> {to_desc}")
        return "\n".join(lines)

    @staticmethod
    def build_method_context(
        vi_context: dict,
        method_name: str,
        class_name: str,
        visibility: str,  # "public", "private", "protected"
        is_static: bool,
        converted_deps: dict[str, VISignature],
        shared_types: list[SharedType],
    ) -> str:
        """Build context for converting a class method.

        Pass the graph database JSON directly - LLMs understand structured data well.

        Args:
            vi_context: Structured VI data from graph.get_vi_context()
            method_name: Name of the method
            class_name: Name of the containing class
            visibility: Method visibility
            is_static: Whether method is static
            converted_deps: Already-converted SubVIs
            shared_types: Relevant shared types

        Returns:
            Complete prompt for LLM
        """
        # Clean the context - filter Void terminals
        cleaned = ContextBuilder._clean_vi_context(vi_context, converted_deps)

        # Build imports section
        import_lines = []
        for sig in converted_deps.values():
            import_lines.append(sig.import_statement)

        if shared_types:
            type_names = ", ".join(t.name for t in shared_types)
            import_lines.append(f"from types import {type_names}")

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
            vi_context_json=json.dumps(cleaned, indent=2),
            class_name=class_name,
            visibility=visibility,
            static_decorator=static_decorator,
            method_name=py_method_name,
            available_imports=available_imports,
        )

    @staticmethod
    def build_error_context(
        code: str,
        errors: list[ValidationError],
        original_prompt: str = "",
    ) -> str:
        """Build context for error correction.

        Args:
            code: The broken Python code
            errors: List of validation errors
            original_prompt: The original conversion prompt for context

        Returns:
            Prompt for LLM to fix errors
        """
        from .validator import ErrorFormatter

        error_text = ErrorFormatter.format(errors)

        context = ""
        if original_prompt:
            context = f"""## Original Requirements
{original_prompt}

---

"""

        return f"""{context}The following Python code has errors that must be fixed:

```python
{code}
```

{error_text}

Fix these errors. Remember:
- Include ALL necessary imports (os, pathlib, etc.)
- Use all primitives from the VI graph
- Handle all SubVIs (call them or add TODO comments)
- Do NOT use stubs or pass statements unless absolutely necessary
- If you must stub something, use: raise NotImplementedError("SubVI: Name.vi")

Output ONLY the corrected Python code, no explanations."""

    @staticmethod
    def build_ui_wrapper(
        vi_name: str,
        module_name: str,
        function_name: str,
        inputs: list[tuple[str, str, str]],  # [(code_name, type, display_name), ...]
        outputs: list[tuple[str, str, str]],
        enums: dict[str, list[tuple[int, str]]] | None = None,
    ) -> str:
        """Generate NiceGUI wrapper for a VI.

        This creates a consistent UI pattern:
        - Input widgets bound to state
        - Execute button
        - Output display widgets

        Args:
            vi_name: Original VI name
            module_name: Python module name (for imports)
            function_name: Python function name
            inputs: List of (code_name, type, display_name) tuples
            outputs: List of (code_name, type, display_name) tuples
            enums: Dict mapping param name to list of (value, label) for dropdowns

        Returns:
            Complete UI wrapper Python code
        """
        enums = enums or {}
        class_name = ContextBuilder._to_class_name(vi_name)

        # Generate input attributes
        input_attrs = []
        for code_name, typ, _ in inputs:
            py_name = ContextBuilder._to_var_name(code_name)
            default = ContextBuilder._get_default(typ)
            input_attrs.append(f"        self.{py_name} = {default}")

        # Generate output attributes
        output_attrs = []
        for code_name, typ, _ in outputs:
            py_name = ContextBuilder._to_var_name(code_name)
            default = ContextBuilder._get_default(typ)
            output_attrs.append(f"        self.{py_name} = {default}")

        # Generate input widgets (5 levels of indentation: class > def > with card > with div > with column)
        input_widgets = []
        for code_name, typ, display_name in inputs:
            py_name = ContextBuilder._to_var_name(code_name)
            # Check if this parameter has enum options
            enum_options = enums.get(code_name)
            widget = ContextBuilder._get_input_widget(display_name, typ, py_name, enum_options)
            input_widgets.append(f"                    {widget}")

        # Generate output widgets (5 levels of indentation)
        output_widgets = []
        for code_name, typ, display_name in outputs:
            py_name = ContextBuilder._to_var_name(code_name)
            widget = ContextBuilder._get_output_widget(display_name, typ, py_name)
            output_widgets.append(f"                    {widget}")

        # Generate function call
        args = ", ".join(f"self.{ContextBuilder._to_var_name(n)}" for n, _, _ in inputs)
        function_call = f"{function_name}({args})"

        # Generate result assignment - all outputs converted to strings for display
        # NiceGUI bindings need JSON-serializable values
        if len(outputs) == 0:
            result_assignment = "pass  # No outputs"
        elif len(outputs) == 1:
            py_name = ContextBuilder._to_var_name(outputs[0][0])
            result_assignment = f"self.{py_name} = str(result[0])"
        else:
            assignments = []
            for i, (code_name, _, _) in enumerate(outputs):
                py_name = ContextBuilder._to_var_name(code_name)
                assignments.append(f"self.{py_name} = str(result[{i}])")
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
    def _format_key_constants(constants: list[dict]) -> str:
        """Format key constants with their Python equivalents prominently.

        This extracts constants that have python hints or meaningful values
        and presents them in a way that's easy for the LLM to use.

        Args:
            constants: List of constant dicts from vi_context

        Returns:
            Formatted string highlighting key constants
        """
        if not constants:
            return "No constants in this VI."

        lines = []
        has_hints = False

        for const in constants:
            value = const.get("value", "")
            python_hint = const.get("python")
            const_type = const.get("type", "")

            if python_hint:
                # This constant has a Python equivalent - highlight it!
                has_hints = True
                lines.append(f"- **{value}**")
                lines.append(f"  Python: `{python_hint}`")
                if const_type:
                    lines.append(f"  Type: {const_type}")
            elif value:
                # Regular constant - show value
                # Try to interpret enum-like values (e.g., "Public Application Data (type 7)")
                if "type" in value.lower() and any(c.isdigit() for c in value):
                    # Looks like a LabVIEW enum/ring value - extract the number
                    import re
                    match = re.search(r'\(type\s*(\d+)\)', value, re.IGNORECASE)
                    if match:
                        type_num = match.group(1)
                        lines.append(f"- **{value}** -> use value `{type_num}` when calling functions")
                        has_hints = True
                        continue
                # Show as-is
                type_info = f" ({const_type})" if const_type else ""
                lines.append(f"- `{value}`{type_info}")

        if not lines:
            return "No constants with special handling needed."

        if has_hints:
            lines.insert(0, "These constants have Python equivalents - USE THEM:")
        else:
            lines.insert(0, "Constants used in this VI:")

        return "\n".join(lines)

    @staticmethod
    def _format_enum_context(enum_context: dict[str, dict] | None) -> str:
        """Format enum context for LLM prompt.

        Args:
            enum_context: Dict mapping control_file -> {name, values, used_values}

        Returns:
            Formatted string describing available enums
        """
        if not enum_context:
            return "No enums/typedefs used in this VI."

        lines = ["Constants with enum/typedef values have Python equivalents:"]

        for control_file, info in enum_context.items():
            name = info.get("name", control_file)
            values = info.get("values", [])
            used_values = info.get("used_values", [])

            lines.append(f"\n**{name}** ({control_file}):")

            # Show values, highlighting used ones
            for val in values:
                idx = val.get("index", 0)
                val_name = val.get("name", f"Value_{idx}")
                python_hint = val.get("python", "")
                windows_path = val.get("windows_path", "")
                unix_path = val.get("unix_path", "")

                marker = "*" if idx in used_values else " "

                if python_hint:
                    lines.append(f"  {marker} {idx}: {val_name} -> `{python_hint}`")
                elif windows_path or unix_path:
                    lines.append(f"  {marker} {idx}: {val_name}")
                    if windows_path:
                        lines.append(f"       Windows: `{windows_path}`")
                    if unix_path:
                        lines.append(f"       Unix: `{unix_path}`")
                else:
                    lines.append(f"  {marker} {idx}: {val_name}")

            if used_values:
                lines.append(f"  (* = used in this VI)")

        return "\n".join(lines)

    @staticmethod
    def _to_function_name(name: str) -> str:
        """Convert VI name to Python function name."""
        # Remove extension
        name = name.replace(".vi", "").replace(".VI", "")
        # Remove lvlib prefix (e.g., "MyLib.lvlib:Function Name" -> "Function Name")
        if ":" in name:
            name = name.split(":")[-1]
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
        # Remove lvlib prefix (e.g., "MyLib.lvlib:Function Name" -> "Function Name")
        if ":" in name:
            name = name.split(":")[-1]
        # Remove .lvlib suffix
        name = name.replace(".lvlib", "")
        # Replace special characters with spaces for word splitting
        for char in "-_.:":
            name = name.replace(char, " ")
        words = name.split()
        # Filter out non-alphanumeric characters from each word
        clean_words = []
        for word in words:
            clean = "".join(c for c in word if c.isalnum())
            if clean:
                clean_words.append(clean)
        return "".join(word.capitalize() for word in clean_words) or "VIClass"

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
        """Get default value for a type (JSON-serializable for NiceGUI)."""
        type_lower = type_str.lower()
        if "int" in type_lower or "i32" in type_lower or "i16" in type_lower:
            return "0"
        if "float" in type_lower or "dbl" in type_lower or "num" in type_lower:
            return "0.0"
        if "bool" in type_lower:
            return "False"
        if "str" in type_lower or "path" in type_lower:
            return "''"
        if "list" in type_lower or "array" in type_lower:
            return "[]"
        # Everything else displays as string in UI
        return "''"

    @staticmethod
    def _get_input_widget(
        label: str,
        type_str: str,
        var_name: str,
        enum_options: list[tuple[int, str]] | None = None,
    ) -> str:
        """Get NiceGUI input widget for a type.

        Uses consistent row layout: label on left, widget on right.
        Clusters render as expandable visual groups.

        Args:
            label: Display label for the widget
            type_str: Python type string
            var_name: Variable name for binding
            enum_options: Optional list of (value, label) tuples for dropdown
        """
        # Check for cluster type - render as visual group
        if "cluster" in type_str.lower() or "dict" in type_str.lower():
            return ContextBuilder._get_cluster_input(label, var_name)

        # Column layout: label above widget
        widget_code = ContextBuilder._get_input_widget_inner(type_str, var_name, enum_options)
        return f"with ui.column().classes('gap-1 w-full'):\n                        ui.label('{label}').classes('text-sm text-gray-600')\n                        {widget_code}"

    @staticmethod
    def _get_input_widget_inner(
        type_str: str,
        var_name: str,
        enum_options: list[tuple[int, str]] | None = None,
    ) -> str:
        """Get the inner widget without label wrapper."""
        # If enum options provided, use a select dropdown
        if enum_options:
            options_dict = {v: f"{v}: {lbl}" for v, lbl in enum_options}
            return f"ui.select({options_dict}).bind_value(self, '{var_name}').classes('flex-1')"

        type_lower = type_str.lower()
        if "bool" in type_lower:
            return f"ui.switch().bind_value(self, '{var_name}')"
        if "int" in type_lower or "float" in type_lower or "num" in type_lower or "dbl" in type_lower:
            return f"ui.number().bind_value(self, '{var_name}').classes('flex-1')"
        if "path" in type_lower:
            return f"ui.input().bind_value(self, '{var_name}').classes('flex-1')"
        # Default to text input
        return f"ui.input().bind_value(self, '{var_name}').classes('flex-1')"

    @staticmethod
    def _get_cluster_input(label: str, var_name: str) -> str:
        """Get cluster input as expandable visual group."""
        return f"""with ui.expansion('{label}').classes('w-full'):
                        ui.textarea().bind_value(self, '{var_name}').classes('w-full font-mono text-xs')"""

    @staticmethod
    def _get_output_widget(label: str, type_str: str, var_name: str) -> str:
        """Get NiceGUI output widget for a type.

        Uses consistent row layout: label on left, value on right.
        Clusters render as expandable visual groups.
        """
        # Check for cluster type - render as visual group
        if "cluster" in type_str.lower() or "dict" in type_str.lower():
            return ContextBuilder._get_cluster_output(label, var_name)

        # Column layout: label above widget (matches input style)
        widget_code = ContextBuilder._get_output_widget_inner(type_str, var_name)
        return f"with ui.column().classes('gap-1 w-full'):\n                        ui.label('{label}').classes('text-sm text-gray-600')\n                        {widget_code}"

    @staticmethod
    def _get_output_widget_inner(type_str: str, var_name: str) -> str:
        """Get the inner output widget without label wrapper."""
        type_lower = type_str.lower()
        if "bool" in type_lower:
            return f"ui.switch().bind_value_from(self, '{var_name}').props('disable')"
        if "int" in type_lower or "float" in type_lower or "num" in type_lower:
            return f"ui.number().bind_value_from(self, '{var_name}').props('readonly').classes('flex-1')"
        # Default to text display with background for output styling
        return f"ui.label().bind_text_from(self, '{var_name}').classes('flex-1 p-2 bg-gray-100 rounded')"

    @staticmethod
    def _get_cluster_output(label: str, var_name: str) -> str:
        """Get cluster output as expandable visual group."""
        return f"""with ui.expansion('{label}').classes('w-full'):
                        ui.label().bind_text_from(self, '{var_name}').classes('w-full font-mono text-xs whitespace-pre-wrap')"""
