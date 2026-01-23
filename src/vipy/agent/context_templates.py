"""LLM prompt templates for VI conversion.

These templates are used by ContextBuilder to generate prompts for the LLM.
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

## Return Values (CRITICAL)
Your function MUST:
1. Check the "outputs" array in the JSON context above
2. Create a NamedTuple with EXACTLY one field for EACH output (check the count!)
3. Return an instance of that NamedTuple with ALL fields populated

Example for outputs "config path" and "error out":
```python
from typing import NamedTuple

class GetSettingsPathResult(NamedTuple):
    config_path: Path  # config path
    error_out: dict  # error out

def get_settings_path(...) -> GetSettingsPathResult:
    ...
    return GetSettingsPathResult(config_path=path, error_out=error)
```

- Count the outputs in JSON carefully - your NamedTuple MUST have that exact number of fields
- Field names: Convert terminal names to snake_case (e.g., "system directory path" -> system_directory_path)
- Add comment with original terminal name after each field
- For cluster outputs (stdClust), use dict type
- For array outputs (indArr), use list type
- For VIs with NO outputs, return None (no NamedTuple needed)

## Common Imports
When using file/path operations, remember to import:
```python
import os  # for os.makedirs, os.path, etc.
from pathlib import Path  # for Path operations
```

## Docstrings (REQUIRED)
Every function MUST have a Google-style docstring with:
1. Brief description of what the VI does
2. Args section listing each parameter with type and description
3. Returns section describing the NamedTuple and its fields

Example:
```python
def get_settings_path(system_directory: int) -> GetSettingsPathResult:
    \"\"\"Get the settings path for the application.

    Args:
        system_directory: The system directory type (0=Home, 1=Desktop, etc.)

    Returns:
        GetSettingsPathResult with:
            config_path: Path to the configuration file
            error_out: Error cluster with status, code, source
    \"\"\"
```

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
