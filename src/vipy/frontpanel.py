"""Parse LabVIEW front panel XML and generate NiceGUI code.

NOTE: FPControl and FrontPanel dataclasses are defined in parser/models.py.
This module provides:
- parse_front_panel() for standalone FP parsing (deprecated, use parse_vi())
- generate_nicegui_code() for NiceGUI UI generation
- summarize_front_panel() for human-readable summaries
"""

from __future__ import annotations

# Import dataclasses from parser
from .parser import FPControl, FrontPanel

# Map LabVIEW DDO classes to NiceGUI widgets
WIDGET_MAP = {
    "stdString": "ui.input",
    "stdNum": "ui.number",
    "stdDBL": "ui.number",
    "stdI32": "ui.number",
    "stdI16": "ui.number",
    "stdU32": "ui.number",
    "stdBool": "ui.switch",
    "stdPath": "ui.input",  # With file picker button
    "stdEnum": "ui.select",
    "stdRing": "ui.select",
    "stdArray": "ui.table",
    "stdCluster": "ui.card",
}


def generate_nicegui_code(
    fp: FrontPanel,
    vi_name: str,
    backend_function: str = "process",
) -> str:
    """Generate NiceGUI frontend code from parsed front panel.

    Args:
        fp: Parsed FrontPanel
        vi_name: Name of the VI (used for class name)
        backend_function: Name of the backend function to call

    Returns:
        Generated Python code string
    """
    # Convert VI name to valid Python class name
    class_name = _to_class_name(vi_name)

    # Separate controls and indicators
    inputs = [c for c in fp.controls if not c.is_indicator]
    outputs = [c for c in fp.controls if c.is_indicator]

    lines = [
        '"""NiceGUI frontend generated from LabVIEW front panel."""',
        "",
        "from nicegui import ui",
        "",
        f"from .backend import {backend_function}",
        "",
        "",
        f"class {class_name}UI:",
        f'    """Frontend for {vi_name}."""',
        "",
        "    def __init__(self):",
        "        # Input controls",
    ]

    # Generate input control instance variables
    for ctrl in inputs:
        var_name = _to_var_name(ctrl.name)
        _generate_init_vars(lines, ctrl, var_name, "        ")

    if outputs:
        lines.append("")
        lines.append("        # Output indicators")
        for ctrl in outputs:
            var_name = _to_var_name(ctrl.name)
            _generate_init_vars(lines, ctrl, var_name, "        ")

    # Generate build method
    lines.extend([
        "",
        "    def build(self):",
        '        """Build the UI."""',
        "        with ui.card().classes('w-96'):",
        f"            ui.label('{vi_name}').classes('text-xl font-bold')",
        "",
        "            # Input controls",
    ])

    # Generate input widgets
    for ctrl in inputs:
        var_name = _to_var_name(ctrl.name)
        widget_code = _generate_widget(ctrl, var_name)
        for line in widget_code:
            lines.append(f"            {line}")
        lines.append("")

    # Generate run button
    lines.extend([
        "            # Execute button",
        "            ui.button('Run', on_click=self.execute).classes('mt-4')",
        "",
    ])

    # Generate output widgets if any
    if outputs:
        lines.extend([
            "            # Output indicators",
            "            ui.separator()",
        ])
        for ctrl in outputs:
            var_name = _to_var_name(ctrl.name)
            widget_code = _generate_indicator(ctrl, var_name)
            for line in widget_code:
                lines.append(f"            {line}")
            lines.append("")

    # Generate execute method
    lines.extend([
        "    async def execute(self):",
        '        """Execute the backend function."""',
        "        try:",
    ])

    # Build function call with inputs
    input_args = ", ".join(f"self.{_to_var_name(c.name)}" for c in inputs)

    if outputs:
        output_vars = ", ".join(f"self.{_to_var_name(c.name)}" for c in outputs)
        lines.append(f"            {output_vars} = {backend_function}({input_args})")
    else:
        lines.append(f"            result = {backend_function}({input_args})")
        lines.append("            ui.notify(f'Completed: {result}')")

    lines.extend([
        "        except Exception as e:",
        "            ui.notify(f'Error: {e}', type='negative')",
        "",
        "",
        "def create_page():",
        '    """Create the NiceGUI page."""',
        f"    app = {class_name}UI()",
        "    app.build()",
        "",
        "",
        "if __name__ in {'__main__', '__mp_main__'}:",
        "    create_page()",
        "    ui.run()",
    ])

    return "\n".join(lines)


def _generate_init_vars(lines: list[str], ctrl: FPControl, var_name: str, indent: str) -> None:
    """Generate instance variable declarations for a control, including cluster children."""
    if ctrl.control_type == "stdClust" and ctrl.children:
        # For clusters, create variables for each child
        for child in ctrl.children:
            child_var = f"{var_name}_{_to_var_name(child.name)}"
            _generate_init_vars(lines, child, child_var, indent)
    else:
        default = _get_default_value(ctrl.control_type)
        lines.append(f"{indent}self.{var_name} = {default}")


def _generate_widget(ctrl: FPControl, var_name: str, indent: int = 0) -> list[str]:
    """Generate widget code for an input control."""
    label = ctrl.name
    pad = "    " * indent

    if ctrl.control_type == "stdClust" and ctrl.children:
        # Render cluster as a card with child controls
        lines = [
            f"{pad}with ui.card().classes('p-2 bg-gray-50'):",
            f"{pad}    ui.label('{label}').classes('font-medium text-sm')",
        ]
        for child in ctrl.children:
            child_var = f"{var_name}_{_to_var_name(child.name)}"
            child_lines = _generate_widget(child, child_var, indent + 1)
            lines.extend(child_lines)
        return lines
    elif ctrl.control_type == "stdPath":
        return [
            f"{pad}with ui.row().classes('items-center'):",
            f"{pad}    ui.input('{label}').bind_value(self, '{var_name}').classes('flex-grow')",
            f"{pad}    ui.button(icon='folder', on_click=lambda: self._browse_{var_name}())",
        ]
    elif ctrl.control_type == "stdBool":
        return [
            f"{pad}ui.switch('{label}').bind_value(self, '{var_name}')",
        ]
    elif ctrl.control_type in ("stdNum", "stdDBL", "stdI32", "stdI16", "stdU32"):
        return [
            f"{pad}ui.number('{label}').bind_value(self, '{var_name}')",
        ]
    elif ctrl.control_type in ("stdEnum", "stdRing"):
        return [
            f"{pad}ui.select([], label='{label}').bind_value(self, '{var_name}')",
        ]
    else:
        # Default to text input
        return [
            f"{pad}ui.input('{label}').bind_value(self, '{var_name}')",
        ]


def _generate_indicator(ctrl: FPControl, var_name: str) -> list[str]:
    """Generate widget code for an output indicator."""
    label = ctrl.name

    if ctrl.control_type in ("stdNum", "stdDBL", "stdI32", "stdI16", "stdU32"):
        return [
            f"ui.label('{label}:')",
            f"ui.label().bind_text_from(self, '{var_name}')",
        ]
    elif ctrl.control_type == "stdBool":
        return [
            f"ui.label('{label}:').bind_visibility_from(self, '{var_name}')",
        ]
    else:
        return [
            f"ui.label('{label}:')",
            f"ui.label().bind_text_from(self, '{var_name}')",
        ]


def _to_class_name(name: str) -> str:
    """Convert VI name to Python class name."""
    # Remove extension, replace spaces/special chars
    name = name.replace(".vi", "").replace(".VI", "")
    words = name.replace("-", " ").replace("_", " ").replace(".", " ").split()
    return "".join(word.capitalize() for word in words)


def _to_var_name(name: str) -> str:
    """Convert control name to Python variable name."""
    result = name.lower()
    # Replace special characters with underscores
    for char in " -.,()[]{}:;'\"!@#$%^&*+=<>?/\\|`~":
        result = result.replace(char, "_")
    # Remove consecutive underscores
    while "__" in result:
        result = result.replace("__", "_")
    # Remove leading/trailing underscores
    result = result.strip("_")
    # Remove leading numbers
    while result and result[0].isdigit():
        result = result[1:]
    return result or "value"


def _get_default_value(control_type: str) -> str:
    """Get default value for a control type."""
    if control_type in ("stdNum", "stdDBL", "stdI32", "stdI16", "stdU32"):
        return "0.0"
    elif control_type == "stdBool":
        return "False"
    elif control_type in ("stdString", "stdPath"):
        return "''"
    elif control_type in ("stdEnum", "stdRing"):
        return "None"
    else:
        return "''"


def summarize_front_panel(fp: FrontPanel) -> str:
    """Generate a human-readable summary of the front panel.

    Args:
        fp: Parsed FrontPanel

    Returns:
        Summary string for LLM processing
    """
    lines = ["FRONT PANEL:", ""]

    inputs = [c for c in fp.controls if not c.is_indicator]
    outputs = [c for c in fp.controls if c.is_indicator]

    if inputs:
        lines.append("INPUTS (Controls):")
        for ctrl in inputs:
            widget = WIDGET_MAP.get(ctrl.control_type, "input")
            lines.append(f'  - {ctrl.name}: {ctrl.control_type} -> {widget}')

    if outputs:
        lines.append("")
        lines.append("OUTPUTS (Indicators):")
        for ctrl in outputs:
            widget = WIDGET_MAP.get(ctrl.control_type, "label")
            lines.append(f'  - {ctrl.name}: {ctrl.control_type} -> {widget}')

    return "\n".join(lines)
