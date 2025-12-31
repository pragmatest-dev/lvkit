"""Parse LabVIEW front panel XML and generate NiceGUI code."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .parser import parse_block_diagram


def _decode_xml_entities_to_bytes(data: str) -> bytes:
    """Convert a string with XML character entities to raw bytes.

    Handles &#xNN; (hex) and &#NNN; (decimal) entities plus literal chars.
    """
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i:i+3] == '&#x':
            # Hex entity: &#xNN;
            end = data.find(';', i)
            if end != -1:
                hex_val = data[i+3:end]
                result.append(int(hex_val, 16))
                i = end + 1
                continue
        elif data[i:i+2] == '&#':
            # Decimal entity: &#NNN;
            end = data.find(';', i)
            if end != -1:
                dec_val = data[i+2:end]
                result.append(int(dec_val))
                i = end + 1
                continue
        # Regular character
        result.append(ord(data[i]) & 0xFF)
        i += 1
    return bytes(result)


def decode_default_data(raw_data: str, control_type: str) -> str | None:
    """Decode DefaultData from FPHb XML to a Python literal.

    DefaultData uses XML character entities (&#xNN;) for binary data.

    Args:
        raw_data: Raw DefaultData string from XML
        control_type: The control type (stdString, stdPath, stdNumeric, etc.)

    Returns:
        Python literal string or None if can't decode
    """
    if not raw_data:
        return None

    # Convert XML entities (&#xNN;) to raw bytes
    try:
        raw_bytes = _decode_xml_entities_to_bytes(raw_data)
    except (ValueError, UnicodeError):
        return None

    # Path: starts with PTH0
    if raw_bytes.startswith(b'PTH0'):
        return _decode_path_default(raw_bytes)

    # String: has length prefix
    if control_type == "stdString" and len(raw_bytes) >= 4:
        return _decode_string_default(raw_bytes)

    # Numeric: typically 4 or 8 bytes
    if control_type in ("stdNumeric", "stdNum"):
        return _decode_numeric_default(raw_bytes)

    # Boolean: single byte
    if control_type == "stdBool" and len(raw_bytes) == 1:
        return "True" if raw_bytes[0] else "False"

    # Cluster: concatenated child values - return as dict placeholder
    # Full parsing requires knowing child types from the cluster definition
    if control_type == "stdClust":
        # For now, return None - cluster defaults need child type info
        return None

    return None


def _decode_path_default(data: bytes) -> str | None:
    """Decode a LabVIEW path from DefaultData bytes."""
    try:
        # Skip PTH0 header (4 bytes) + length (4 bytes) + flags (4 bytes)
        idx = 12
        parts = []
        while idx < len(data):
            str_len = data[idx]
            idx += 1
            if str_len > 0 and idx + str_len <= len(data):
                part = data[idx:idx + str_len].decode('latin-1', errors='replace')
                parts.append(part)
                idx += str_len
            else:
                break
        if parts:
            path_str = '/'.join(parts)
            return f'Path("{path_str}")'
    except (IndexError, ValueError):
        pass
    return None


def _decode_string_default(data: bytes) -> str | None:
    """Decode a LabVIEW string from DefaultData bytes."""
    try:
        if len(data) < 4:
            return None
        length = int.from_bytes(data[:4], 'big')
        if len(data) >= 4 + length:
            string_val = data[4:4 + length].decode('latin-1')
            escaped = string_val.replace('\\', '\\\\').replace('"', '\\"')
            return f'"{escaped}"'
    except (ValueError, UnicodeDecodeError):
        pass
    return None


def _decode_numeric_default(data: bytes) -> str | None:
    """Decode a numeric value from DefaultData bytes."""
    try:
        if len(data) == 4:
            # 32-bit integer (big-endian)
            return str(int.from_bytes(data, 'big', signed=True))
        elif len(data) == 8:
            # Could be 64-bit int or float
            import struct
            try:
                float_val = struct.unpack('>d', data)[0]
                if float_val == int(float_val):
                    return str(int(float_val))
                return str(float_val)
            except struct.error:
                return str(int.from_bytes(data, 'big', signed=True))
    except (ValueError, struct.error):
        pass
    return None


@dataclass
class FPControl:
    """A control or indicator on the front panel."""
    uid: str
    name: str
    control_type: str  # stdString, stdNumeric, stdBool, stdPath, stdEnum, etc.
    bounds: tuple[int, int, int, int]  # top, left, bottom, right
    is_indicator: bool = False  # True if output, False if input
    type_desc: str | None = None
    default_value: str | None = None
    enum_values: list[str] = field(default_factory=list)
    children: list[FPControl] = field(default_factory=list)  # For clusters


@dataclass
class FrontPanel:
    """Parsed front panel representation."""
    controls: list[FPControl]
    panel_bounds: tuple[int, int, int, int]
    title: str | None = None


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


def parse_front_panel(
    fp_xml_path: Path | str,
    bd_xml_path: Path | str | None = None,
) -> FrontPanel:
    """Parse a pylabview front panel XML file.

    Args:
        fp_xml_path: Path to the *_FPHb.xml file
        bd_xml_path: Path to the *_BDHb.xml file (optional, for accurate input/output detection)

    Returns:
        FrontPanel with extracted controls and indicators
    """
    fp_xml_path = Path(fp_xml_path)
    tree = ET.parse(fp_xml_path)
    root = tree.getroot()

    # If block diagram provided, use wire analysis for is_indicator detection
    indicator_dco_uids: set[str] = set()
    if bd_xml_path:
        bd = parse_block_diagram(bd_xml_path)
        for fp_term in bd.fp_terminals:
            if fp_term.is_indicator:
                indicator_dco_uids.add(fp_term.fp_dco_uid)

    controls = []

    # Get panel bounds
    pbounds_elem = root.find("pBounds")
    if pbounds_elem is not None and pbounds_elem.text:
        panel_bounds = _parse_bounds(pbounds_elem.text)
    else:
        panel_bounds = (0, 0, 400, 600)

    # Find all front panel data control objects (fPDCO)
    # They appear as SL__arrayElement with class='fPDCO'
    for fpdco in root.findall(".//*[@class='fPDCO']"):
        uid = fpdco.get("uid", "")

        # Get the data display object (ddo) which has the control type
        ddo = fpdco.find("ddo")
        if ddo is None:
            # Try direct child with class attribute starting with 'std'
            for child in fpdco:
                child_class = child.get("class", "")
                if child_class.startswith("std") or child_class == "typeDef":
                    ddo = child
                    break

        if ddo is None:
            continue

        # Extract and decode default data from fPDCO
        default_value = None
        default_elem = fpdco.find("DefaultData")
        if default_elem is not None and default_elem.text:
            raw_data = default_elem.text.strip('"')
            control_type = ddo.get("class", "unknown")
            default_value = decode_default_data(raw_data, control_type)

        control = _parse_ddo(ddo, uid, indicator_dco_uids, default_value)
        if control:
            controls.append(control)

    return FrontPanel(
        controls=controls,
        panel_bounds=panel_bounds,
    )


def _parse_ddo(
    ddo: ET.Element,
    uid: str,
    indicator_dco_uids: set[str],
    default_data: str | None = None,
) -> FPControl | None:
    """Parse a data display object (ddo) into an FPControl.

    Handles nested structures like clusters and typeDefs recursively.

    Args:
        ddo: The data display object XML element
        uid: Control UID
        indicator_dco_uids: Set of UIDs that are indicators
        default_data: Default value data (may be complex for clusters)
    """
    control_type = ddo.get("class", "unknown")

    # For typeDef, look inside for the actual control
    if control_type == "typeDef":
        # Find the inner control (usually stdClust or other std* type)
        inner_ddo = None
        for child in ddo.findall(".//*"):
            child_class = child.get("class", "")
            if child_class.startswith("std"):
                inner_ddo = child
                break
        if inner_ddo is not None:
            # Get the typeDef's label but parse the inner control
            name = _extract_label(ddo) or f"control_{uid}"
            inner_control = _parse_ddo(inner_ddo, uid, indicator_dco_uids, default_data)
            if inner_control:
                inner_control.name = name  # Use typeDef's name
                return inner_control
        return None

    # Get bounds
    bounds_elem = ddo.find("bounds")
    if bounds_elem is not None and bounds_elem.text:
        bounds = _parse_bounds(bounds_elem.text)
    else:
        bounds = (0, 0, 100, 200)

    # Get label/name
    name = _extract_label(ddo) or f"control_{uid}"

    # Determine if indicator (output) using block diagram wire analysis if available
    if indicator_dco_uids:
        is_indicator = uid in indicator_dco_uids
    else:
        # Fallback to objFlags heuristic
        obj_flags = ddo.find("objFlags")
        is_indicator = False
        if obj_flags is not None and obj_flags.text:
            try:
                flags = int(obj_flags.text)
                is_indicator = bool(flags & 0x10000)
            except ValueError:
                pass

    # Parse children for clusters
    children = []
    if control_type == "stdClust":
        # Find all child controls within the cluster
        # TODO: Parse cluster default_data structure to get child defaults
        for child_elem in ddo.findall(".//*"):
            child_class = child_elem.get("class", "")
            if child_class.startswith("std") and child_class != "stdClust":
                # Skip if this is a nested element we already processed
                child_uid = child_elem.get("uid", "")
                if child_uid:
                    child_control = _parse_ddo(child_elem, child_uid, set(), None)
                    if child_control:
                        children.append(child_control)

    return FPControl(
        uid=uid,
        name=name,
        control_type=control_type,
        bounds=bounds,
        is_indicator=is_indicator,
        default_value=default_data,
        children=children,
    )


def _parse_bounds(bounds_str: str) -> tuple[int, int, int, int]:
    """Parse bounds string like '(0, 0, 100, 200)' to tuple."""
    try:
        # Remove parentheses and split
        clean = bounds_str.strip("()")
        parts = [int(x.strip()) for x in clean.split(",")]
        if len(parts) == 4:
            return tuple(parts)
    except (ValueError, AttributeError):
        pass
    return (0, 0, 100, 200)


def _extract_label(elem: ET.Element) -> str | None:
    """Extract label text from a control element."""
    # Try partsList with class='label' (common structure)
    for part in elem.findall(".//*[@class='label']"):
        text_elem = part.find(".//text")
        if text_elem is not None and text_elem.text:
            text = text_elem.text.strip('"')
            # Skip generic labels like "Pane"
            if text.lower() not in ("pane", ""):
                return text

    # Try textRec directly
    text_elem = elem.find(".//textRec/text")
    if text_elem is not None and text_elem.text:
        text = text_elem.text.strip('"')
        if text.lower() not in ("pane", ""):
            return text

    return None


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
