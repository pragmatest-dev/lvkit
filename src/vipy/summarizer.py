"""Generate human-readable summaries of VIs for LLM processing."""

from __future__ import annotations

from pathlib import Path

from .parser import BlockDiagram, Constant, Node, parse_block_diagram, parse_vi_metadata


# Known LabVIEW system directory types
SYSTEM_DIR_TYPES = {
    0: "User Home",
    1: "User Desktop",
    2: "User Documents",
    3: "User Application Data",
    4: "User Preferences",
    5: "User Temporary",
    6: "Public Documents",
    7: "Public Application Data",
    8: "Public Preferences",
    9: "System Core Libraries",
    10: "System Installed Libraries",
    11: "Application Files",
    12: "Boot Volume Root",
}

# Known primitive mappings (primResID -> description)
# This is a starting point - needs expansion
PRIMITIVE_MAP = {
    1419: "Build Path",
    1420: "Strip Path",
    # Add more as discovered
}


def decode_labview_path(hex_value: str) -> str:
    """Decode a LabVIEW path constant from hex."""
    try:
        data = bytes.fromhex(hex_value)
        if not data.startswith(b'PTH0'):
            return hex_value  # Not a path, return as-is

        # Skip header and metadata, parse length-prefixed strings
        idx = 12  # Skip PTH0 + length + flags
        parts = []
        while idx < len(data):
            str_len = data[idx]
            idx += 1
            if str_len > 0 and idx + str_len <= len(data):
                parts.append(data[idx:idx + str_len].decode('ascii', errors='replace'))
                idx += str_len
            else:
                break
        return '/'.join(parts)
    except Exception:
        return hex_value


def decode_constant(const: Constant, context_hint: str | None = None) -> tuple[str, str]:
    """Decode a constant value to (type, human-readable value).

    Args:
        const: The constant to decode
        context_hint: Optional hint about what this constant connects to

    Returns:
        Tuple of (type_name, human_readable_value)
    """
    value = const.value

    # Check for path constant
    if value.startswith('50544830'):  # 'PTH0' in hex
        return ("path", decode_labview_path(value))

    # Check for small integer (enum/numeric)
    if len(value) == 8:
        try:
            int_val = int(value, 16)
            # Check if it's a system directory type based on context or label
            label_lower = (const.label or "").lower()
            context_lower = (context_hint or "").lower()
            if ("system directory" in label_lower or
                "system directory" in context_lower or
                "get system directory" in context_lower):
                dir_name = SYSTEM_DIR_TYPES.get(int_val, "unknown")
                return ("system_dir_type", f"{dir_name} ({int_val})")
            return ("int", str(int_val))
        except ValueError:
            pass

    return ("raw", value)


def summarize_vi(bd_xml_path: Path | str, main_xml_path: Path | str | None = None) -> str:
    """Generate a summary of a VI for LLM processing.

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)
        main_xml_path: Path to the main VI XML (optional, for metadata)

    Returns:
        Human-readable summary string
    """
    bd = parse_block_diagram(bd_xml_path)

    # Get metadata if available
    vi_name = "Unknown VI"
    subvi_refs = []
    if main_xml_path:
        meta = parse_vi_metadata(main_xml_path)
        vi_name = meta.get("name", vi_name)
        subvi_refs = meta.get("subvi_refs", [])

    lines = [f'LabVIEW VI: "{vi_name}"', ""]

    # Describe nodes
    lines.append("NODES:")
    for i, node in enumerate(bd.nodes, 1):
        if node.node_type == "iUse":
            lines.append(f'  {i}. SubVI: "{node.name}"')
        elif node.node_type == "prim":
            prim_name = PRIMITIVE_MAP.get(node.prim_res_id, f"Primitive #{node.prim_index}")
            lines.append(f"  {i}. {prim_name} (primResID={node.prim_res_id})")
        elif node.node_type == "whileLoop":
            lines.append(f"  {i}. While Loop")
        elif node.node_type == "forLoop":
            lines.append(f"  {i}. For Loop")
        elif node.node_type == "select":
            lines.append(f"  {i}. Case/Select Structure")
        elif node.node_type == "propNode":
            lines.append(f'  {i}. Property Node: "{node.name}"')
        elif node.node_type in ("seq", "caseStruct", "eventStruct"):
            lines.append(f"  {i}. {node.node_type}")

    # Describe constants
    if bd.constants:
        lines.append("")
        lines.append("CONSTANTS:")
        for const in bd.constants:
            val_type, val = decode_constant(const)
            label = const.label or f"constant_{const.uid}"
            lines.append(f'  - {label}: {val} ({val_type})')

    # Describe data flow
    lines.append("")
    lines.append("WIRES (data flow):")
    for wire in bd.wires:
        lines.append(f"  {wire.from_term} -> {wire.to_term}")

    # List SubVI dependencies
    if subvi_refs:
        lines.append("")
        lines.append("DEPENDENCIES (SubVIs called):")
        for ref in subvi_refs:
            lines.append(f"  - {ref}")

    return "\n".join(lines)


def create_llm_prompt(summary: str) -> str:
    """Create a full prompt for the LLM to convert the VI to Python.

    Args:
        summary: VI summary from summarize_vi()

    Returns:
        Complete prompt string
    """
    return f"""{summary}

Convert this LabVIEW VI to an equivalent Python function.
- Use os.path for path operations
- Use os.makedirs with exist_ok=True for directory creation
- Map system directories to appropriate Python equivalents (e.g., APPDATA, HOME)
- Output ONLY the Python code, no explanations."""
