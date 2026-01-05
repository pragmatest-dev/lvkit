"""Unified VI parsing - single entry point for all VI components.

Architecture:
- parse_vi() is the single entry point
- Returns ParsedVI containing all components
- Pure XML extraction, no external lookups
- Resolution/enrichment happens in memory_graph.py
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from vipy.constants import (
    MULTI_LABEL_CLASS,
    OPERATION_NODE_CLASSES,
    TERMINAL_CLASS,
    TERMINAL_CONTAINER_CLASSES,
)
from vipy.graph_types import LVType

from .front_panel import (
    _lvtype_to_parsed,
    extract_fp_terminals,
    parse_connector_pane,
    parse_connector_pane_types,
)
from .models import (
    BlockDiagram,
    ConnectorPane,
    Constant,
    FPControl,
    FPTerminal,
    FrontPanel,
    Node,
    ParsedVI,
    ParsedType,
    SubVIPathRef,
    TerminalInfo,
    VIMetadata,
    Wire,
)
from .nodes import extract_constants, extract_loops
from .types import parse_type_map_rich, resolve_type_rich


def parse_vi(
    vi_path: Path | str | None = None,
    *,
    bd_xml: Path | str | None = None,
    fp_xml: Path | str | None = None,
    main_xml: Path | str | None = None,
) -> ParsedVI:
    """Parse a VI file into all components.

    This is the single entry point for VI parsing. Returns a ParsedVI
    containing metadata, block diagram, front panel, and connector pane.

    Args:
        vi_path: Path to .vi file (extracts XML automatically)
        bd_xml: Path to *_BDHb.xml (for direct XML parsing)
        fp_xml: Path to *_FPHb.xml (optional)
        main_xml: Path to main *.xml (optional)

    Returns:
        ParsedVI with all components
    """
    # Extract XML from VI file if needed
    if vi_path is not None and bd_xml is None:
        from vipy.extractor import extract_vi_xml
        bd_xml, fp_xml, main_xml = extract_vi_xml(vi_path)

    if bd_xml is None:
        raise ValueError("Either vi_path or bd_xml must be provided")

    bd_xml = Path(bd_xml)

    # Derive source .vi path from BD XML path
    source_path = bd_xml.with_name(bd_xml.name.replace("_BDHb.xml", ".vi"))
    source_path_str = str(source_path) if source_path.exists() else None

    # Parse metadata from main XML
    metadata = _parse_metadata(main_xml, source_path_str)

    # Parse block diagram
    block_diagram = _parse_block_diagram(bd_xml, fp_xml, metadata.type_map)

    # Parse front panel
    front_panel = _parse_front_panel(fp_xml, block_diagram)

    # Parse connector pane
    connector_pane = None
    if fp_xml:
        fp_xml_path = Path(fp_xml)
        if fp_xml_path.exists():
            connector_pane = parse_connector_pane(fp_xml_path)

    return ParsedVI(
        metadata=metadata,
        block_diagram=block_diagram,
        front_panel=front_panel,
        connector_pane=connector_pane,
    )


def _parse_metadata(
    main_xml_path: Path | str | None,
    source_path: str | None,
) -> VIMetadata:
    """Parse VI metadata from main XML."""
    if main_xml_path is None:
        return VIMetadata(source_path=source_path)

    main_xml = Path(main_xml_path)
    if not main_xml.exists():
        return VIMetadata(source_path=source_path)

    main_tree = ET.parse(main_xml)
    main_root = main_tree.getroot()

    # Extract qualified name from LVIN or LVSR
    qualified_name: str | None = None
    lvin = main_root.find(".//LIvi/Section/LVIN")
    if lvin is not None:
        qualified_name = lvin.get("Unk1")
    if not qualified_name:
        lvsr = main_root.find(".//LVSR/Section")
        if lvsr is not None:
            qualified_name = lvsr.get("Name")

    # Extract SubVI info
    subvi_qualified_names, iuse_to_qualified_name, subvi_path_refs = _extract_subvi_info(
        main_root, qualified_name
    )

    # Parse type map
    type_map = parse_type_map_rich(main_xml)

    return VIMetadata(
        qualified_name=qualified_name,
        source_path=source_path,
        type_map=type_map or {},
        subvi_qualified_names=subvi_qualified_names,
        iuse_to_qualified_name=iuse_to_qualified_name,
        subvi_path_refs=subvi_path_refs,
    )


def _parse_block_diagram(
    bd_xml: Path,
    fp_xml: Path | str | None,
    type_map: dict[int, LVType] | None,
) -> BlockDiagram:
    """Parse block diagram from BD XML."""
    tree = ET.parse(bd_xml)
    root = tree.getroot()

    nodes = _extract_nodes(root)
    constants = extract_constants(root)
    wires = _extract_wires(root)
    fp_terminals = extract_fp_terminals(root, fp_xml, type_map)
    enum_labels = _extract_enum_labels(root)
    terminal_info = _extract_terminal_info(root, constants, fp_terminals, wires, type_map)
    loops = extract_loops(root)

    return BlockDiagram(
        nodes=nodes,
        constants=constants,
        wires=wires,
        fp_terminals=fp_terminals,
        enum_labels=enum_labels,
        terminal_info=terminal_info,
        loops=loops,
    )


def _parse_front_panel(
    fp_xml: Path | str | None,
    block_diagram: BlockDiagram,
) -> FrontPanel:
    """Parse front panel from FP XML."""
    if fp_xml is None:
        return FrontPanel(controls=[], panel_bounds=(0, 0, 400, 600))

    fp_xml_path = Path(fp_xml)
    if not fp_xml_path.exists():
        return FrontPanel(controls=[], panel_bounds=(0, 0, 400, 600))

    tree = ET.parse(fp_xml_path)
    root = tree.getroot()

    # Build indicator UIDs from block diagram for accurate is_indicator detection
    indicator_dco_uids: set[str] = set()
    for fp_term in block_diagram.fp_terminals:
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
    for fpdco in root.findall(".//*[@class='fPDCO']"):
        uid = fpdco.get("uid", "")

        # Get the data display object (ddo) which has the control type
        ddo = fpdco.find("ddo")
        if ddo is None:
            for child in fpdco:
                child_class = child.get("class", "")
                if child_class.startswith("std") or child_class == "typeDef":
                    ddo = child
                    break

        if ddo is None:
            continue

        # Extract default data
        default_value = None
        default_elem = fpdco.find("DefaultData")
        if default_elem is not None and default_elem.text:
            raw_data = default_elem.text.strip('"')
            control_type = ddo.get("class", "unknown")
            default_value = _decode_default_data(raw_data, control_type)

        control = _parse_ddo(ddo, uid, indicator_dco_uids, default_value)
        if control:
            controls.append(control)

    return FrontPanel(
        controls=controls,
        panel_bounds=panel_bounds,
    )


# === Helper functions ===


def _extract_nodes(root: ET.Element) -> list[Node]:
    """Extract nodes from the block diagram using node type factory."""
    from .node_types import parse_node

    nodes = []

    for cls in OPERATION_NODE_CLASSES:
        for elem in root.findall(f".//*[@class='{cls}']"):
            node = parse_node(elem)
            nodes.append(node)

    return nodes


def _extract_wires(root: ET.Element) -> list[Wire]:
    """Extract wires (signals) from the block diagram."""
    wires = []

    for sig in root.findall(".//signalList/SL__arrayElement[@class='signal']"):
        uid = sig.get("uid")
        terms = [t.get("uid") for t in sig.findall("termList/SL__arrayElement")]

        if len(terms) >= 2:
            source = terms[0]
            for i, dest in enumerate(terms[1:]):
                wires.append(Wire(
                    uid=f"{uid}_{i}" if i > 0 else uid,
                    from_term=source,
                    to_term=dest,
                ))

    return wires


def _extract_enum_labels(root: ET.Element) -> dict[str, list[str]]:
    """Extract enum/ring labels from the XML."""
    enums: dict[str, list[str]] = {}
    for multi_label in root.findall(f".//*[@class='{MULTI_LABEL_CLASS}']"):
        buf = multi_label.find("buf")
        if buf is not None and buf.text:
            text = buf.text
            labels = []
            i = 0
            if text.startswith("("):
                i = text.find(")") + 1
            while i < len(text):
                if text[i] == '"':
                    end = text.find('"', i + 1)
                    if end > i:
                        labels.append(text[i + 1:end])
                        i = end + 1
                    else:
                        break
                else:
                    i += 1
            if labels:
                parent = multi_label
                while parent is not None:
                    uid = parent.get("uid")
                    if uid:
                        enums[uid] = labels
                        break
                    break
    return enums


def _extract_terminal_info(
    root: ET.Element,
    constants: list[Constant],
    fp_terminals: list[FPTerminal],
    wires: list[Wire],
    type_map: dict[int, LVType] | None = None,
) -> dict[str, TerminalInfo]:
    """Extract detailed terminal info for graph-native representation."""
    terminal_info: dict[str, TerminalInfo] = {}

    # Build wire connectivity maps for direction inference
    wire_sources: set[str] = {w.from_term for w in wires}
    wire_sinks: set[str] = {w.to_term for w in wires}

    # Extract terminals from operation nodes
    for elem in root.iter():
        elem_uid = elem.get("uid")
        elem_class = elem.get("class", "")

        if not elem_uid:
            continue

        if elem_class in TERMINAL_CONTAINER_CLASSES:
            term_list = elem.findall(f"./termList/SL__arrayElement[@class='{TERMINAL_CLASS}']")

            for list_position, term in enumerate(term_list):
                term_uid = term.get("uid")
                if not term_uid:
                    continue

                dco = term.find("dco")

                # Get parmIndex from dco if present
                parm_index = list_position
                if dco is not None:
                    parm_index_elem = dco.find("parmIndex")
                    if parm_index_elem is not None and parm_index_elem.text:
                        parm_index = int(parm_index_elem.text)

                # Determine direction from wire connectivity
                if term_uid in wire_sources:
                    is_output = True
                elif term_uid in wire_sinks:
                    is_output = False
                else:
                    # Unwired terminal - fall back to flag-based detection
                    term_flags_elem = term.find("objFlags")
                    term_flags = int(term_flags_elem.text) if term_flags_elem is not None and term_flags_elem.text else 0
                    dco_flags = 0
                    if dco is not None:
                        dco_flags_elem = dco.find("objFlags")
                        dco_flags = int(dco_flags_elem.text) if dco_flags_elem is not None and dco_flags_elem.text else 0
                    combined_flags = term_flags | dco_flags
                    is_output = bool(combined_flags & 0x1)

                # Resolve TypeID to ParsedType
                type_desc_elem = term.find(".//typeDesc")
                type_desc_str = type_desc_elem.text if type_desc_elem is not None else None
                parsed_type = None
                if type_desc_str and type_map:
                    lv_type = resolve_type_rich(type_desc_str, type_map)
                    parsed_type = _lvtype_to_parsed(lv_type)

                terminal_info[term_uid] = TerminalInfo(
                    uid=term_uid,
                    parent_uid=elem_uid,
                    index=parm_index,
                    is_output=is_output,
                    parsed_type=parsed_type,
                )

    # Constants have a single output terminal
    for const in constants:
        if const.uid not in terminal_info:
            parsed_type = None
            if const.type_desc and type_map:
                lv_type = resolve_type_rich(const.type_desc, type_map)
                parsed_type = _lvtype_to_parsed(lv_type)

            terminal_info[const.uid] = TerminalInfo(
                uid=const.uid,
                parent_uid=const.uid,
                index=0,
                is_output=True,
                parsed_type=parsed_type,
            )

    # Front panel terminals
    for fp_term in fp_terminals:
        if fp_term.uid not in terminal_info:
            terminal_info[fp_term.uid] = TerminalInfo(
                uid=fp_term.uid,
                parent_uid=fp_term.uid,
                index=0,
                is_output=not fp_term.is_indicator,
                parsed_type=fp_term.parsed_type,
                name=fp_term.name,
            )

    return terminal_info


def _extract_subvi_info(
    main_root: ET.Element,
    caller_qualified_name: str | None,
) -> tuple[list[str], dict[str, str], list[SubVIPathRef]]:
    """Extract SubVI qualified names, iUse→qualified_name mapping, and path refs."""
    subvi_qualified_names: list[str] = []
    iuse_to_qualified_name: dict[str, str] = {}
    subvi_path_refs: list[SubVIPathRef] = []

    # Get caller's library for qualifying same-library references
    caller_library = None
    if caller_qualified_name and ":" in caller_qualified_name:
        caller_library = caller_qualified_name.split(":")[0]

    # Extract SubVI qualified names from VIVI entries
    for vivi in main_root.findall(".//LIvi//VIVI"):
        strings = [s.text for s in vivi.findall("LinkSaveQualName/String") if s.text]
        if strings:
            link_save_flag = vivi.get("LinkSaveFlag", "0")
            if link_save_flag == "2" and caller_library and len(strings) == 1:
                qname = f"{caller_library}:{strings[0]}"
            else:
                qname = ":".join(strings)
            subvi_qualified_names.append(qname)

            # Build path ref for file resolution
            name = strings[-1] if strings[-1].endswith(".vi") else None
            if name:
                path_elem = vivi.find("LinkSavePath/String")
                path_text = path_elem.text if path_elem is not None else ""
                path_parts = [p for p in path_text.split("/") if p] if path_text else []
                is_vilib = path_text.startswith("<vilib>") if path_text else False
                is_userlib = path_text.startswith("<userlib>") if path_text else False
                subvi_path_refs.append(SubVIPathRef(
                    name=name,
                    path_tokens=path_parts,
                    is_vilib=is_vilib,
                    is_userlib=is_userlib,
                    qualified_name=qname,
                ))

    # Also include polymorphic VIs (VIPV)
    for vipv in main_root.findall(".//LIvi//VIPV"):
        strings = [s.text for s in vipv.findall("LinkSaveQualName/String") if s.text]
        if strings:
            link_save_flag = vipv.get("LinkSaveFlag", "0")
            if link_save_flag == "2" and caller_library and len(strings) == 1:
                qname = f"{caller_library}:{strings[0]}"
            else:
                qname = ":".join(strings)
            subvi_qualified_names.append(qname)

    # Extract iUse UID → qualified name map from BDHP section
    for iuvi in main_root.findall(".//LIbd//BDHP/IUVI"):
        strings = [s.text for s in iuvi.findall("LinkSaveQualName/String") if s.text]
        if strings:
            link_save_flag = iuvi.get("LinkSaveFlag", "0")
            if link_save_flag == "2" and caller_library and len(strings) == 1:
                qname = f"{caller_library}:{strings[0]}"
            else:
                qname = ":".join(strings)
            for offset_elem in iuvi.findall("LinkOffsetList/Offset"):
                if offset_elem.text:
                    uid = str(int(offset_elem.text, 16))
                    iuse_to_qualified_name[uid] = qname

    # Also handle polymorphic iUse (PUPV)
    for pupv in main_root.findall(".//LIbd//BDHP/PUPV"):
        strings = [s.text for s in pupv.findall("LinkSaveQualName/String") if s.text]
        if strings:
            link_save_flag = pupv.get("LinkSaveFlag", "0")
            if link_save_flag == "2" and caller_library and len(strings) == 1:
                qname = f"{caller_library}:{strings[0]}"
            else:
                qname = ":".join(strings)
            for offset_elem in pupv.findall("LinkOffsetList/Offset"):
                if offset_elem.text:
                    uid = str(int(offset_elem.text, 16))
                    iuse_to_qualified_name[uid] = qname

    return subvi_qualified_names, iuse_to_qualified_name, subvi_path_refs


# === Front panel parsing helpers ===


def _parse_bounds(bounds_str: str) -> tuple[int, int, int, int]:
    """Parse bounds string like '(0, 0, 100, 200)' to tuple."""
    try:
        clean = bounds_str.strip("()")
        parts = [int(x.strip()) for x in clean.split(",")]
        if len(parts) == 4:
            return tuple(parts)  # type: ignore
    except (ValueError, AttributeError):
        pass
    return (0, 0, 100, 200)


def _extract_label(elem: ET.Element) -> str | None:
    """Extract label text from a control element."""
    for part in elem.findall(".//*[@class='label']"):
        text_elem = part.find(".//text")
        if text_elem is not None and text_elem.text:
            text = text_elem.text.strip('"')
            if text.lower() not in ("pane", ""):
                return text

    text_elem = elem.find(".//textRec/text")
    if text_elem is not None and text_elem.text:
        text = text_elem.text.strip('"')
        if text.lower() not in ("pane", ""):
            return text

    return None


def _parse_ddo(
    ddo: ET.Element,
    uid: str,
    indicator_dco_uids: set[str],
    default_data: str | None = None,
) -> FPControl | None:
    """Parse a data display object (ddo) into an FPControl."""
    control_type = ddo.get("class", "unknown")

    # For typeDef, look inside for the actual control
    if control_type == "typeDef":
        inner_ddo = None
        for child in ddo.findall(".//*"):
            child_class = child.get("class", "")
            if child_class.startswith("std"):
                inner_ddo = child
                break
        if inner_ddo is not None:
            name = _extract_label(ddo) or f"control_{uid}"
            inner_control = _parse_ddo(inner_ddo, uid, indicator_dco_uids, default_data)
            if inner_control:
                inner_control.name = name
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

    # Determine if indicator
    if indicator_dco_uids:
        is_indicator = uid in indicator_dco_uids
    else:
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
        for child_elem in ddo.findall(".//*"):
            child_class = child_elem.get("class", "")
            if child_class.startswith("std") and child_class != "stdClust":
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


def _decode_xml_entities_to_bytes(data: str) -> bytes:
    """Convert a string with XML character entities to raw bytes."""
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i:i+3] == '&#x':
            end = data.find(';', i)
            if end != -1:
                hex_val = data[i+3:end]
                result.append(int(hex_val, 16))
                i = end + 1
                continue
        elif data[i:i+2] == '&#':
            end = data.find(';', i)
            if end != -1:
                dec_val = data[i+2:end]
                result.append(int(dec_val))
                i = end + 1
                continue
        result.append(ord(data[i]) & 0xFF)
        i += 1
    return bytes(result)


def _decode_default_data(raw_data: str, control_type: str) -> str | None:
    """Decode DefaultData from FPHb XML to a Python literal."""
    if not raw_data:
        return None

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

    return None


def _decode_path_default(data: bytes) -> str | None:
    """Decode a LabVIEW path from DefaultData bytes."""
    try:
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
            return str(int.from_bytes(data, 'big', signed=True))
        elif len(data) == 8:
            import struct
            try:
                float_val = struct.unpack('>d', data)[0]
                if float_val == int(float_val):
                    return str(int(float_val))
                return str(float_val)
            except struct.error:
                return str(int.from_bytes(data, 'big', signed=True))
    except (ValueError,):
        pass
    return None


