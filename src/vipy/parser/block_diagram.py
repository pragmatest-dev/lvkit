"""Block diagram parsing - main orchestrator."""

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

from .front_panel import _lvtype_to_parsed, extract_fp_terminals
from .models import (
    BlockDiagram,
    Constant,
    FPTerminal,
    Node,
    ParsedType,
    SubVIPathRef,
    TerminalInfo,
    Wire,
)
from .nodes import extract_constants, extract_loops
from .nodes.base import extract_label, extract_terminal_types
from .types import parse_type_map_rich, resolve_type_rich


def parse_block_diagram(
    xml_path: Path | str,
    fp_xml_path: Path | str | None = None,
    main_xml_path: Path | str | None = None,
) -> BlockDiagram:
    """Parse a pylabview block diagram XML file.

    Args:
        xml_path: Path to the *_BDHb.xml file
        fp_xml_path: Optional path to the *_FPHb.xml file (for extracting typeDesc from FP DCOs)
        main_xml_path: Optional path to the main .xml file (for type resolution)

    Returns:
        BlockDiagram with extracted nodes, constants, and wires (types as ParsedType)
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Load type map and qualified name from main XML (single parse)
    type_map: dict[int, LVType] | None = None
    qualified_name: str | None = None
    subvi_qualified_names: list[str] = []
    iuse_to_qualified_name: dict[str, str] = {}
    subvi_path_refs: list[SubVIPathRef] = []

    if main_xml_path:
        main_xml = Path(main_xml_path)
        if main_xml.exists():
            # Parse main XML once for all extractions
            main_tree = ET.parse(main_xml)
            main_root = main_tree.getroot()

            # Extract qualified name from LVIN or LVSR
            lvin = main_root.find(".//LIvi/Section/LVIN")
            if lvin is not None:
                qualified_name = lvin.get("Unk1")
            if not qualified_name:
                lvsr = main_root.find(".//LVSR/Section")
                if lvsr is not None:
                    qualified_name = lvsr.get("Name")

            # Extract SubVI qualified names, iUse→qualified_name map, and path refs
            subvi_qualified_names, iuse_to_qualified_name, subvi_path_refs = _extract_subvi_info(main_root)

            # Parse type map
            type_map = parse_type_map_rich(main_xml)

    nodes = _extract_nodes(root)
    constants = extract_constants(root)
    wires = _extract_wires(root)
    fp_terminals = extract_fp_terminals(root, fp_xml_path, type_map)
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
        qualified_name=qualified_name,
        subvi_qualified_names=subvi_qualified_names,
        iuse_to_qualified_name=iuse_to_qualified_name,
        type_map=type_map or {},
        subvi_path_refs=subvi_path_refs,
    )


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
    """Extract wires (signals) from the block diagram.

    In LabVIEW, a single signal can connect one source to multiple destinations.
    We create separate Wire objects for each destination.
    """
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
    """Extract enum/ring labels from the XML.

    Returns:
        Dict mapping UID to list of enum labels
    """
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
    """Extract detailed terminal info for graph-native representation.

    Captures:
    - Terminal position (index) in parent's termList
    - Input vs output direction (from wire connectivity)
    - Type information (resolved to ParsedType)

    Args:
        root: XML root element
        constants: List of parsed constants
        fp_terminals: List of front panel terminals
        wires: List of parsed wires (for direction inference)
        type_map: Optional type map for resolving TypeID references

    Returns:
        Dict mapping terminal UID to TerminalInfo with ParsedType
    """
    terminal_info: dict[str, TerminalInfo] = {}

    # Build wire connectivity maps for direction inference
    # from_term = source of data = OUTPUT terminal
    # to_term = destination of data = INPUT terminal
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
                # If terminal is source of a wire (from_term) -> OUTPUT
                # If terminal is sink of a wire (to_term) -> INPUT
                # Wire connectivity is the authoritative source for SubVI terminals
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
            # Resolve constant type
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

    # Front panel terminals (already have ParsedType from extract_fp_terminals)
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


def _extract_subvi_info(main_root: ET.Element) -> tuple[list[str], dict[str, str], list[SubVIPathRef]]:
    """Extract SubVI qualified names, iUse→qualified_name mapping, and path refs from main XML.

    Args:
        main_root: Root element of main .xml file

    Returns:
        Tuple of:
        - subvi_qualified_names: List of unique SubVI qualified names from VIVI entries
        - iuse_to_qualified_name: Dict mapping iUse UID to qualified name from BDHP
        - subvi_path_refs: List of SubVIPathRef for file resolution
    """
    subvi_qualified_names: list[str] = []
    iuse_to_qualified_name: dict[str, str] = {}
    subvi_path_refs: list[SubVIPathRef] = []

    # Get caller's library for qualifying same-library references
    caller_library = None
    lvin = main_root.find(".//LIvi/Section/LVIN")
    if lvin is not None:
        caller_qname = lvin.get("Unk1")
        if caller_qname and ":" in caller_qname:
            caller_library = caller_qname.split(":")[0]

    # Extract SubVI qualified names from VIVI entries
    # LinkSaveFlag="2" means same-library reference (needs caller's library prepended)
    # LinkSaveFlag="0" means different library (already has full qualified name)
    for vivi in main_root.findall(".//LIvi//VIVI"):
        strings = [s.text for s in vivi.findall("LinkSaveQualName/String") if s.text]
        if strings:
            link_save_flag = vivi.get("LinkSaveFlag", "0")
            if link_save_flag == "2" and caller_library and len(strings) == 1:
                # Same-library reference - prepend caller's library
                qname = f"{caller_library}:{strings[0]}"
            else:
                qname = ":".join(strings)
            subvi_qualified_names.append(qname)

            # Also build path ref for file resolution
            # Get the VI name (last string)
            name = strings[-1] if strings[-1].endswith(".vi") else None
            if name:
                # Extract path tokens from LinkSavePath
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
    # LinkOffsetList/Offset values are iUse UIDs in hex
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
                    # Convert hex offset to decimal string (= iUse UID)
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
