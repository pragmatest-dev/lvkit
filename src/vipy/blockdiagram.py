"""Parse and summarize LabVIEW block diagrams."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .constants import SYSTEM_DIR_TYPES
from .graph_types import LVType
from .parser import (
    Constant,
    parse_vi,
)
from .parser.node_types import PrimitiveNode
from .parser.vi import _decode_element
from .type_defaults import get_default_for_type

# === Primitive Tracking ===
# Tracks primitives encountered during parsing (for analysis)
_primitives_seen: dict[int, dict] = {}  # primResID -> {count, contexts, vi_names}


def register_primitive(prim_res_id: int, vi_name: str = "", context: str = "") -> None:
    """Register a primitive encountered during parsing.

    Args:
        prim_res_id: The primitive resource ID
        vi_name: Name of the VI where this primitive was found
        context: Any contextual info (connected wires, types, etc.)
    """
    if prim_res_id not in _primitives_seen:
        _primitives_seen[prim_res_id] = {
            "count": 0,
            "vi_names": set(),
            "contexts": set(),
        }

    _primitives_seen[prim_res_id]["count"] += 1
    if vi_name:
        _primitives_seen[prim_res_id]["vi_names"].add(vi_name)
    if context:
        _primitives_seen[prim_res_id]["contexts"].add(context)


def get_primitives_seen() -> dict[int, dict]:
    """Get all primitives encountered.

    Returns:
        Dict mapping primResID -> {count, vi_names, contexts}
    """
    return {
        pid: {
            "count": info["count"],
            "vi_names": list(info["vi_names"]),
            "contexts": list(info["contexts"])[:5],
        }
        for pid, info in sorted(_primitives_seen.items())
    }


def clear_primitives_seen() -> None:
    """Clear the primitives registry."""
    _primitives_seen.clear()


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
    except (ValueError, IndexError, UnicodeDecodeError):
        return hex_value


def decode_labview_string(hex_value: str) -> str | None:
    """Decode a LabVIEW string constant from hex.

    LabVIEW strings have a 4-byte big-endian length prefix followed by data.
    """
    try:
        data = bytes.fromhex(hex_value)
        if len(data) < 4:
            return None
        length = int.from_bytes(data[:4], 'big')
        if len(data) >= 4 + length:
            return data[4:4 + length].decode('latin-1')
        return None
    except (ValueError, UnicodeDecodeError):
        return None


def decode_constant(
    const: Constant,
    context_hint: str | None = None,
    lv_type: LVType | None = None,
) -> tuple[str, str]:
    """Decode a constant value to (type, human-readable value).

    Args:
        const: The constant to decode
        context_hint: Optional hint about what this constant connects to
        lv_type: LVType from the graph (authoritative type info)

    Returns:
        Tuple of (type_name, human_readable_value)
    """
    value = const.value

    # Type-aware decoding. LabVIEW is typed — we always have the type.
    if lv_type is not None:
        raw_bytes = bytes.fromhex(value)

        # Standalone boolean constants can be 2 bytes (1 byte in arrays).
        # Any nonzero byte means True.
        underlying = getattr(lv_type, "underlying_type", "")
        if underlying == "Boolean" and len(raw_bytes) > 1:
            return (lv_type.to_python(), "True" if any(raw_bytes) else "False")

        decoded, _ = _decode_element(raw_bytes, lv_type)
        py_type = lv_type.to_python()

        if decoded is not None:
            return (py_type, decoded)

        # Couldn't parse bytes — use the type's default value.
        return (py_type, get_default_for_type(lv_type))

    # No type = parser bug. Return raw.
    return ("raw", value)


def summarize_vi(
    bd_xml_path: Path | str, main_xml_path: Path | str | None = None
) -> str:
    """Generate a summary of a VI for LLM processing.

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)
        main_xml_path: Path to the main VI XML (optional, for metadata)

    Returns:
        Human-readable summary string
    """
    bd_xml_path = Path(bd_xml_path)
    vi = parse_vi(bd_xml=bd_xml_path, main_xml=main_xml_path)
    bd = vi.block_diagram
    metadata = vi.metadata

    # Get VI name from metadata
    vi_name = metadata.qualified_name or bd_xml_path.stem.replace("_BDHb", "")
    subvi_refs = metadata.subvi_qualified_names

    lines = [f'LabVIEW VI: "{vi_name}"', ""]

    # Build UID-to-node map for semantic wire descriptions
    uid_to_node = {}
    uid_to_const = {}

    for node in bd.nodes:
        uid_to_node[node.uid] = node
    for const in bd.constants:
        uid_to_const[const.uid] = const

    # Describe nodes with Python equivalents
    lines.append("OPERATIONS:")
    node_refs = {}  # Map UID to readable reference
    for i, node in enumerate(bd.nodes, 1):
        ref = f"[{i}]"
        node_refs[node.uid] = ref

        if node.node_type == "iUse":
            lines.append(f'  {ref} SubVI: "{node.name}"')
        elif node.node_type == "prim" and isinstance(node, PrimitiveNode):
            # Include primResID and terminal types - LLM infers meaning from context
            type_info = ""
            if node.input_types or node.output_types:
                inputs = ", ".join(node.input_types) if node.input_types else "none"
                outputs = ", ".join(node.output_types) if node.output_types else "none"
                type_info = f" (inputs: [{inputs}], outputs: [{outputs}])"
            lines.append(f"  {ref} Primitive #{node.prim_res_id}{type_info}")
        elif node.node_type == "whileLoop":
            lines.append(f"  {ref} While Loop: while condition:")
        elif node.node_type == "forLoop":
            lines.append(f"  {ref} For Loop: for i in range(n):")
        elif node.node_type == "select":
            lines.append(f"  {ref} Case/Select: if/elif/else structure")
        elif node.node_type == "propNode":
            lines.append(f'  {ref} Property Node: "{node.name}"')
        elif node.node_type in ("seq", "caseStruct", "eventStruct"):
            lines.append(f"  {ref} {node.node_type}")

    # Describe constants with context
    if bd.constants:
        lines.append("")
        lines.append("CONSTANTS:")
        for const in bd.constants:
            const_ref = f"const_{const.uid}"
            node_refs[const.uid] = const_ref

            # Check if this is an enum value with known labels
            enum_label = _get_enum_value_label(const, bd.enum_labels)

            if enum_label:
                lines.append(f'  - {const_ref}: {enum_label}')
            else:
                val_type, val = decode_constant(const)
                if const.label:
                    lines.append(f'  - {const_ref} ({const.label}): {val} ({val_type})')
                else:
                    lines.append(f'  - {const_ref}: {val} ({val_type})')

    # Build semantic data flow description
    lines.append("")
    lines.append("DATA FLOW:")

    for wire in bd.wires:
        from_parent = bd.term_to_parent.get(wire.from_term, wire.from_term)
        to_parent = bd.term_to_parent.get(wire.to_term, wire.to_term)

        from_desc = _describe_terminal(
            from_parent, node_refs, uid_to_node, uid_to_const
        )
        to_desc = _describe_terminal(
            to_parent, node_refs, uid_to_node, uid_to_const
        )

        lines.append(f"  {from_desc} -> {to_desc}")

    # List SubVI dependencies
    if subvi_refs:
        lines.append("")
        lines.append("DEPENDENCIES (SubVIs called):")
        for ref in subvi_refs:
            lines.append(f"  - {ref}")

    return "\n".join(lines)


def _get_enum_value_label(
    const: Constant, enum_labels: dict[str, list[str]]
) -> str | None:
    """Get the label for an enum constant value."""
    try:
        if len(const.value) == 8:
            int_val = int(const.value, 16)
            # Check if this constant has associated enum labels
            if const.uid in enum_labels:
                labels = enum_labels[const.uid]
                if 0 <= int_val < len(labels):
                    return f"{labels[int_val]} (enum value {int_val})"
            # Check for system directory type pattern
            label_lower = (const.label or "").lower()
            if "system directory" in label_lower or int_val in SYSTEM_DIR_TYPES:
                dir_info = SYSTEM_DIR_TYPES.get(int_val)
                if dir_info:
                    name, win_env, unix_path = dir_info
                    return (
                        f"{name} (type {int_val}) -> Python:"
                        f" os.environ['{win_env}'] on Windows,"
                        f" '{unix_path}' on Unix"
                    )
    except (ValueError, TypeError):
        pass
    return None


def _describe_terminal(
    parent_uid: str, node_refs: dict, uid_to_node: dict, uid_to_const: dict
) -> str:
    """Create a human-readable description of a terminal."""
    if parent_uid in node_refs:
        return node_refs[parent_uid]

    if parent_uid in uid_to_node:
        node = uid_to_node[parent_uid]
        if node.node_type == "iUse" and node.name:
            return f'"{node.name}"'
        elif node.node_type == "prim" and node.prim_res_id:
            return f"prim#{node.prim_res_id}"
        return f"node_{parent_uid}"

    if parent_uid in uid_to_const:
        const = uid_to_const[parent_uid]
        return f"const_{const.uid}"

    return f"term_{parent_uid}"


def _get_fp_terminal_names(bd_xml_path: Path) -> dict[str, str]:
    """Get front panel terminal names from the FPHb.xml file.

    Args:
        bd_xml_path: Path to the block diagram XML (*_BDHb.xml)

    Returns:
        Dict mapping DCO uid to terminal name
    """
    # Derive front panel XML path from block diagram path
    fp_xml_path = (
        bd_xml_path.parent / bd_xml_path.name.replace("_BDHb.xml", "_FPHb.xml")
    )

    if not fp_xml_path.exists():
        return {}

    try:
        tree = ET.parse(fp_xml_path)
        root = tree.getroot()

        names = {}
        # Find fPDCO elements (front panel data container objects)
        for fp_dco in root.findall(".//*[@class='fPDCO']"):
            uid = fp_dco.get("uid")
            if not uid:
                continue

            # Look for the label inside the ddo's partsList
            # fPDCO/ddo/partsList/SL__arrayElement[@class='label']/textRec/text
            ddo = fp_dco.find("ddo")
            if ddo is not None:
                # Try multiple paths where label might be
                label_elem = None
                for label in ddo.findall(".//*[@class='label']"):
                    text_elem = label.find(".//textRec/text")
                    if text_elem is not None and text_elem.text:
                        label_elem = text_elem
                        break

                if label_elem is not None and label_elem.text:
                    names[uid] = label_elem.text.strip('"')

        return names
    except (ET.ParseError, FileNotFoundError, OSError):
        return {}


def create_llm_prompt(
    summary: str, mode: str = "script", summary_format: str = "text"
) -> str:
    """Create a full prompt for the LLM to convert the VI to Python.

    Args:
        summary: VI summary from summarize() or cypher.from_blockdiagram()
        mode: "script" for standalone, "gui" for backend function
        summary_format: "text" or "cypher"

    Returns:
        Complete prompt string
    """
    if mode == "gui":
        return f"""{summary}

Convert this LabVIEW VI to a Python backend function called from a NiceGUI frontend.
- Create a single function that takes the INPUT controls as parameters
- Return the OUTPUT indicators as a tuple (or single value if one output)
- Use os.path for path operations
- Use os.makedirs with exist_ok=True for directory creation
- Map system directories to appropriate Python equivalents (e.g., APPDATA, HOME)
- The function should be importable (no if __name__ == '__main__')
- Output ONLY the Python code, no explanations."""

    return f"""{summary}

Convert this LabVIEW VI to an equivalent Python function.
- Use os.path for path operations
- Use os.makedirs with exist_ok=True for directory creation
- Map system directories to appropriate Python equivalents (e.g., APPDATA, HOME)
- Output ONLY the Python code, no explanations."""

