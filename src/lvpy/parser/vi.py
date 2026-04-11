"""Unified VI parsing - single entry point for all VI components.

Architecture:
- parse_vi() is the single entry point
- Returns ParsedVI containing all components
- Pure XML extraction, no external lookups
- Resolution/enrichment happens in lvpy.graph (InMemoryVIGraph)
"""

from __future__ import annotations

import json
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from lvpy.constants import (
    MULTI_LABEL_CLASS,
    NODE_CLASS_SHIFT_REG,
    OPERATION_NODE_CLASSES,
    STRUCTURE_NODE_CLASSES,
    TERMINAL_CLASS,
    TERMINAL_CONTAINER_CLASSES,
)
from lvpy.extractor import extract_vi_xml
from lvpy.graph_types import LVType

from ..type_defaults import get_default_for_type
from .flags import is_indicator, is_output_terminal
from .front_panel import (
    _lvtype_to_parsed,
    extract_fp_terminals,
    parse_connector_pane,
)
from .models import (
    ParsedBlockDiagram,
    ParsedConstant,
    ParsedFPControl,
    ParsedFPTerminal,
    ParsedFrontPanel,
    ParsedNode,
    ParsedSubVIPathRef,
    ParsedTerminalInfo,
    ParsedVI,
    ParsedVIMetadata,
    ParsedWire,
)
from .node_types import parse_node
from .nodes import (
    extract_case_structures,
    extract_constants,
    extract_flat_sequences,
    extract_loops,
)
from .type_mapping import parse_type_map_rich
from .type_resolution import resolve_type_rich
from .utils import clean_labview_string, extract_label, safe_int


def _load_node_dco_maps() -> dict[str, dict[str, int]]:
    """Load DCO maps from primitives.json node_types terminals.

    Builds {node_class: {dco_ref_tag: terminal_index}} from terminals
    that have a dco_ref field. Same terminal structure as primitives.

    Returns: {node_class: {dco_ref_tag: terminal_index}}
    """
    from .._data import data_dir as _bundled_data_dir
    primitives_path = _bundled_data_dir() / "primitives.json"
    if not primitives_path.exists():
        return {}
    with open(primitives_path) as f:
        data = json.load(f)
    result = {}
    for node_type, info in data.get("node_types", {}).items():
        dco_map = {}
        for t in info.get("terminals", []):
            ref = t.get("dco_ref")
            if ref:
                dco_map[ref] = t["index"]
        if dco_map:
            result[node_type] = dco_map
    return result


# Loaded once at import time
_NODE_DCO_MAP: dict[str, dict[str, int]] = _load_node_dco_maps()


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
        bd_xml, fp_xml, main_xml = extract_vi_xml(vi_path)

    if bd_xml is None:
        raise ValueError("Either vi_path or bd_xml must be provided")

    bd_xml = Path(bd_xml)

    # Derive source .vi path. Prefer the explicit vi_path argument since BD XML
    # may now live in a temp cache dir rather than next to the source file.
    if vi_path is not None:
        source_path_str = str(Path(vi_path).resolve())
    else:
        source_path = bd_xml.with_name(bd_xml.name.replace("_BDHb.xml", ".vi"))
        source_path_str = str(source_path) if source_path.exists() else None

    # Parse metadata from main XML
    metadata = _parse_metadata(main_xml, source_path_str)

    # Parse block diagram
    block_diagram = _parse_block_diagram(bd_xml, fp_xml, metadata.type_map)

    # Parse front panel
    front_panel = _parse_front_panel(fp_xml, block_diagram, metadata.type_map)

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
) -> ParsedVIMetadata:
    """Parse VI metadata from main XML."""
    if main_xml_path is None:
        return ParsedVIMetadata(source_path=source_path)

    main_xml = Path(main_xml_path)
    if not main_xml.exists():
        return ParsedVIMetadata(source_path=source_path)

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
    (
        subvi_qualified_names,
        iuse_to_qualified_name,
        subvi_path_refs,
    ) = _extract_subvi_info(main_root, qualified_name)

    # Parse type map
    type_map = parse_type_map_rich(main_xml)

    return ParsedVIMetadata(
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
) -> ParsedBlockDiagram:
    """Parse block diagram from BD XML."""
    tree = ET.parse(bd_xml)
    root = tree.getroot()

    nodes = _extract_nodes(root)
    constants = extract_constants(root)
    wires = _extract_wires(root)
    fp_terminals = extract_fp_terminals(root, fp_xml, type_map)
    enum_labels = _extract_enum_labels(root)
    srn_to_structure: dict[str, str] = {}
    terminal_info = _extract_terminal_info(
        root, constants, fp_terminals, wires, type_map,
        srn_to_structure=srn_to_structure,
    )
    loops = extract_loops(root)
    case_structures = extract_case_structures(root, terminal_info)
    flat_sequences = extract_flat_sequences(root)

    return ParsedBlockDiagram(
        nodes=nodes,
        constants=constants,
        wires=wires,
        fp_terminals=fp_terminals,
        enum_labels=enum_labels,
        terminal_info=terminal_info,
        loops=loops,
        case_structures=case_structures,
        flat_sequences=flat_sequences,
        srn_to_structure=srn_to_structure,
    )


def _parse_front_panel(
    fp_xml: Path | str | None,
    block_diagram: ParsedBlockDiagram,
    type_map: dict[int, LVType] | None = None,
) -> ParsedFrontPanel:
    """Parse front panel from FP XML."""
    if fp_xml is None:
        return ParsedFrontPanel(controls=[], panel_bounds=(0, 0, 400, 600))

    fp_xml_path = Path(fp_xml)
    if not fp_xml_path.exists():
        return ParsedFrontPanel(controls=[], panel_bounds=(0, 0, 400, 600))

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
            raw_data = clean_labview_string(default_elem.text)
            control_type = ddo.get("class", "unknown")

            # Resolve type for array/cluster decoding
            lv_type = None
            type_desc_elem = fpdco.find("typeDesc")
            if type_desc_elem is not None and type_desc_elem.text and type_map:
                lv_type = resolve_type_rich(type_desc_elem.text, type_map)

            default_value = _decode_default_data(raw_data, control_type, lv_type)

        control = _parse_ddo(ddo, uid, indicator_dco_uids, default_value)
        if control:
            controls.append(control)

    return ParsedFrontPanel(
        controls=controls,
        panel_bounds=panel_bounds,
    )


# === Helper functions ===


def _extract_nodes(root: ET.Element) -> list[ParsedNode]:
    """Extract nodes from the block diagram using node type factory."""
    nodes = []

    for cls in OPERATION_NODE_CLASSES:
        for elem in root.findall(f".//*[@class='{cls}']"):
            node = parse_node(elem)
            nodes.append(node)

    return nodes


def _extract_wires(root: ET.Element) -> list[ParsedWire]:
    """Extract wires (signals) from the block diagram."""
    wires = []

    for sig in root.findall(".//signalList/SL__arrayElement[@class='signal']"):
        uid = sig.get("uid") or ""
        terms: list[str] = [
            t_uid
            for t in sig.findall("termList/SL__arrayElement")
            if (t_uid := t.get("uid"))
        ]

        if len(terms) >= 2:
            source = terms[0]
            for i, dest in enumerate(terms[1:]):
                wires.append(ParsedWire(
                    uid=f"{uid}_{i}" if i > 0 else uid,
                    from_term=source,
                    to_term=dest,
                ))

    return wires


def _extract_enum_labels(root: ET.Element) -> dict[str, list[str]]:
    """Extract enum/ring labels from the XML.

    Parses multi-label buffers like '(10)"Label1""Label2""Label3"'
    where labels are quoted strings.
    """
    enums: dict[str, list[str]] = {}
    for multi_label in root.findall(f".//*[@class='{MULTI_LABEL_CLASS}']"):
        buf = multi_label.find("buf")
        if buf is not None and buf.text:
            # Extract all quoted strings using regex
            labels = re.findall(r'"([^"]*)"', buf.text)
            if labels:
                uid = multi_label.get("uid")
                if uid:
                    enums[uid] = labels
    return enums


def _process_element_terminals(
    elem: ET.Element,
    wire_sources: set[str],
    wire_sinks: set[str],
    type_map: dict[int, LVType] | None,
    terminal_info: dict[str, ParsedTerminalInfo],
) -> None:
    """Extract terminals from a single TERMINAL_CONTAINER_CLASSES element."""
    elem_uid = elem.get("uid") or ""
    elem_class = elem.get("class", "")

    term_list = elem.findall(
        f"./termList/SL__arrayElement[@class='{TERMINAL_CLASS}']",
    )

    for list_position, term in enumerate(term_list):
        term_uid = term.get("uid")
        if not term_uid:
            continue

        dco = term.find("dco")
        dco_uid = dco.get("uid") if dco is not None else None

        # Get terminal index from dco.
        # Primitives use "parmIndex", SubVIs use "paramIdx".
        # Missing paramIdx = 0 (XML omits the default value).
        # Missing parmIndex on primitives = genuinely unknown (-1).
        parm_index = -1
        if dco is not None:
            for idx_field in ("parmIndex", "paramIdx"):
                idx_elem = dco.find(idx_field)
                if idx_elem is not None and idx_elem.text:
                    parm_index = int(idx_elem.text)
                    break
            else:
                # No index field found. XML omits parmIndex when it's 0.
                # Applies to SubVI calls AND primitives.
                if elem_class in ("iUse", "polyIUse", "dynIUse", "prim"):
                    parm_index = 0

        # For specialized node classes (aDelete, aIndx, etc.),
        # resolve index from named DCO references on the parent node.
        if parm_index == -1 and dco_uid and elem_class in _NODE_DCO_MAP:
            dco_map = _NODE_DCO_MAP[elem_class]
            for ref_tag, ref_index in dco_map.items():
                ref_elem = elem.find(ref_tag)
                if ref_elem is not None:
                    # Direct ref: element has uid matching dco
                    if ref_elem.get("uid") == dco_uid:
                        parm_index = ref_index
                        break
                    # List ref (dcoList, lengthDCOList): children
                    # have uids. Position in list = dimension.
                    # Stride by number of list-type refs to interleave.
                    for pos, child in enumerate(ref_elem):
                        if child.get("uid") == dco_uid:
                            # Count how many list-type refs exist
                            # to determine stride for interleaving
                            n_lists = sum(
                                1 for rt in dco_map
                                if (rt_elem := elem.find(rt)) is not None
                                and len(rt_elem) > 0
                            )
                            parm_index = ref_index + (pos * max(n_lists, 1))
                            break
                if parm_index >= 0:
                    break

        # Last resort: use list position as index.
        # Covers sRN terminals, printf expandable terminals, and any
        # other terminal type without explicit parmIndex in the XML.
        # The termList order IS the natural index.
        if parm_index == -1:
            parm_index = list_position

        # Determine direction from wire connectivity
        if term_uid in wire_sources:
            is_output = True
        elif term_uid in wire_sinks:
            is_output = False
        else:
            # Unwired terminal - fall back to flag-based detection
            term_flags = safe_int(term.find("objFlags"))
            dco_obj = dco.find("objFlags") if dco is not None else None
            dco_flags = safe_int(dco_obj)
            combined_flags = term_flags | dco_flags
            is_output = is_output_terminal(combined_flags)

        # Resolve TypeID to ParsedType
        type_desc_elem = term.find(".//typeDesc")
        type_desc_str = (
            type_desc_elem.text if type_desc_elem is not None
            else None
        )
        parsed_type = None
        if type_desc_str and type_map:
            lv_type = resolve_type_rich(type_desc_str, type_map)
            parsed_type = _lvtype_to_parsed(lv_type)

        # Extract terminal label from dco or terminal element
        term_name = None
        if dco is not None:
            term_name = extract_label(dco)
        if not term_name:
            term_name = extract_label(term)

        terminal_info[term_uid] = ParsedTerminalInfo(
            uid=term_uid,
            parent_uid=elem_uid,
            index=parm_index,
            is_output=is_output,
            parsed_type=parsed_type,
            name=term_name,
        )


def _walk_and_extract_terminals(
    elem: ET.Element,
    wire_sources: set[str],
    wire_sinks: set[str],
    type_map: dict[int, LVType] | None,
    terminal_info: dict[str, ParsedTerminalInfo],
    srn_to_structure: dict[str, str],
    current_structure_uid: str | None,
) -> None:
    """Walk XML tree, extracting terminals and tracking sRN containment."""
    elem_uid = elem.get("uid")
    elem_class = elem.get("class", "")

    # Extract terminals from this element if it's a terminal container
    if elem_uid and elem_class in TERMINAL_CONTAINER_CLASSES:
        _process_element_terminals(
            elem, wire_sources, wire_sinks, type_map, terminal_info,
        )

    # Record sRN → structure containment
    if elem_uid and elem_class == NODE_CLASS_SHIFT_REG and current_structure_uid:
        srn_to_structure[elem_uid] = current_structure_uid

    # Update structure context for children
    if elem_uid and elem_class in STRUCTURE_NODE_CLASSES:
        next_structure_uid = elem_uid
    else:
        next_structure_uid = current_structure_uid

    # Recurse into children
    for child in elem:
        _walk_and_extract_terminals(
            child, wire_sources, wire_sinks, type_map,
            terminal_info, srn_to_structure, next_structure_uid,
        )


def _extract_terminal_info(
    root: ET.Element,
    constants: list[ParsedConstant],
    fp_terminals: list[ParsedFPTerminal],
    wires: list[ParsedWire],
    type_map: dict[int, LVType] | None = None,
    srn_to_structure: dict[str, str] | None = None,
) -> dict[str, ParsedTerminalInfo]:
    """Extract detailed terminal info for graph-native representation.

    Walks the XML tree hierarchically to preserve structure containment.
    Populates srn_to_structure (if provided) mapping sRN UIDs to their
    containing structure UIDs.
    """
    terminal_info: dict[str, ParsedTerminalInfo] = {}
    if srn_to_structure is None:
        srn_to_structure = {}

    # Build wire connectivity maps for direction inference
    wire_sources: set[str] = {w.from_term for w in wires}
    wire_sinks: set[str] = {w.to_term for w in wires}

    # Walk XML hierarchically — preserves structure containment for sRN nodes
    _walk_and_extract_terminals(
        root, wire_sources, wire_sinks, type_map,
        terminal_info, srn_to_structure, None,
    )

    # Constants have a single output terminal
    for const in constants:
        if const.uid not in terminal_info:
            parsed_type = None
            if const.type_desc and type_map:
                lv_type = resolve_type_rich(const.type_desc, type_map)
                parsed_type = _lvtype_to_parsed(lv_type)

            terminal_info[const.uid] = ParsedTerminalInfo(
                uid=const.uid,
                parent_uid=const.uid,
                index=0,
                is_output=True,
                parsed_type=parsed_type,
            )

    # Front panel terminals
    for fp_term in fp_terminals:
        if fp_term.uid not in terminal_info:
            terminal_info[fp_term.uid] = ParsedTerminalInfo(
                uid=fp_term.uid,
                parent_uid=fp_term.uid,
                index=0,
                is_output=not fp_term.is_indicator,
                parsed_type=fp_term.parsed_type,
                name=fp_term.name,
            )

    return terminal_info


def _resolve_qualified_name(
    elem: ET.Element,
    caller_library: str | None,
) -> str | None:
    """Resolve qualified name from an element with LinkSaveQualName.

    Handles LinkSaveFlag to determine if same-library qualification is needed.

    Args:
        elem: Element with LinkSaveQualName and LinkSaveFlag attributes
        caller_library: Library name of the calling VI, for same-library refs

    Returns:
        Qualified name string, or None if no name found
    """
    strings = [s.text for s in elem.findall("LinkSaveQualName/String") if s.text]
    if not strings:
        return None

    # Strip control characters and XML entities from all strings
    strings = [clean_labview_string(s) for s in strings]
    strings = [s for s in strings if s]  # Remove any that became empty
    if not strings:
        return None

    link_save_flag = elem.get("LinkSaveFlag", "0")
    # Flag "2" means same-library reference - qualify with caller's library
    if link_save_flag == "2" and caller_library and len(strings) == 1:
        return f"{caller_library}:{strings[0]}"
    return ":".join(strings)


def _extract_subvi_info(
    main_root: ET.Element,
    caller_qualified_name: str | None,
) -> tuple[list[str], dict[str, str], list[ParsedSubVIPathRef]]:
    """Extract SubVI qualified names, iUse→qualified_name mapping, and path refs."""
    subvi_qualified_names: list[str] = []
    iuse_to_qualified_name: dict[str, str] = {}
    subvi_path_refs: list[ParsedSubVIPathRef] = []

    # Get caller's library for qualifying same-library references
    caller_library = None
    if caller_qualified_name and ":" in caller_qualified_name:
        caller_library = caller_qualified_name.split(":")[0]

    # Extract SubVI qualified names from VIVI entries
    for vivi in main_root.findall(".//LIvi//VIVI"):
        qname = _resolve_qualified_name(vivi, caller_library)
        if qname:
            subvi_qualified_names.append(qname)

            # Build path ref for file resolution
            strings = [
                s.text for s in vivi.findall("LinkSaveQualName/String")
                if s.text
            ]
            name = strings[-1] if strings and strings[-1].endswith(".vi") else None
            if name:
                # Extract path from LinkSavePathRef (multiple String elements)
                path_parts = [
                    s.text for s in vivi.findall("LinkSavePathRef/String")
                    if s.text
                ]
                is_vilib = path_parts[0] == "<vilib>" if path_parts else False
                is_userlib = path_parts[0] == "<userlib>" if path_parts else False
                subvi_path_refs.append(ParsedSubVIPathRef(
                    name=name,
                    path_tokens=path_parts,
                    is_vilib=is_vilib,
                    is_userlib=is_userlib,
                    qualified_name=qname,
                ))

    # VIPI entries (dynamic dispatch VI calls - class methods)
    for vipi in main_root.findall(".//LIvi//VIPI"):
        qname = _resolve_qualified_name(vipi, caller_library)
        if qname:
            subvi_qualified_names.append(qname)

    # DyOM entries (dynamic dispatch method references)
    for dyom in main_root.findall(".//LIvi//DyOM"):
        qname = _resolve_qualified_name(dyom, caller_library)
        if qname:
            subvi_qualified_names.append(qname)

    # Also include polymorphic VIs (VIPV)
    for vipv in main_root.findall(".//LIvi//VIPV"):
        qname = _resolve_qualified_name(vipv, caller_library)
        if qname:
            subvi_qualified_names.append(qname)

    # Extract iUse UID → qualified name map from BDHP section.
    # Process PUPV (polymorphic wrapper) first, then IUVI (resolved variant)
    # overwrites — the variant is the actual VI to connect to.
    for pupv in main_root.findall(".//LIbd//BDHP/PUPV"):
        qname = _resolve_qualified_name(pupv, caller_library)
        if qname:
            for offset_elem in pupv.findall("LinkOffsetList/Offset"):
                if offset_elem.text:
                    uid = str(int(offset_elem.text, 16))
                    iuse_to_qualified_name[uid] = qname

    for iuvi in main_root.findall(".//LIbd//BDHP/IUVI"):
        qname = _resolve_qualified_name(iuvi, caller_library)
        if qname:
            for offset_elem in iuvi.findall("LinkOffsetList/Offset"):
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


def _parse_ddo(
    ddo: ET.Element,
    uid: str,
    indicator_dco_uids: set[str],
    default_data: str | None = None,
) -> ParsedFPControl | None:
    """Parse a data display object (ddo) into a ParsedFPControl."""
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
            name = extract_label(ddo) or f"control_{uid}"
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
    name = extract_label(ddo) or f"control_{uid}"

    # Determine if indicator
    if indicator_dco_uids:
        control_is_indicator = uid in indicator_dco_uids
    else:
        flags = safe_int(ddo.find("objFlags"))
        control_is_indicator = is_indicator(flags)

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

    return ParsedFPControl(
        uid=uid,
        name=name,
        control_type=control_type,
        bounds=bounds,
        is_indicator=control_is_indicator,
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


def _decode_default_data(
    raw_data: str,
    control_type: str,
    lv_type: LVType | None = None,
) -> str | None:
    """Decode DefaultData from FPHb XML to a Python literal.

    Uses _decode_element (the single type-aware decoder) when lv_type
    is available. Falls back to control_type dispatch only when no
    type info exists.
    """
    if not raw_data:
        return None

    try:
        raw_bytes = _decode_xml_entities_to_bytes(raw_data)
    except (ValueError, UnicodeError):
        return None

    # Use the type-aware decoder when we have type info
    if lv_type is not None:
        decoded, _ = _decode_element(raw_bytes, lv_type)
        if decoded is not None:
            return decoded

    # Fallback: dispatch by control_type string (no type info)
    if raw_bytes.startswith(b'PTH0'):
        return _decode_path_default(raw_bytes)
    if control_type == "stdString" and len(raw_bytes) >= 4:
        return _decode_string_default(raw_bytes)
    if control_type in ("stdNumeric", "stdNum"):
        return _decode_numeric_default(raw_bytes)
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


def _decode_array_default(data: bytes, lv_type: LVType) -> str | None:
    """Decode an array default value from DefaultData bytes.

    Format: 4-byte length + elements (each element encoded by type)
    """
    if len(data) < 4:
        return None

    try:
        # Get array length
        array_len = int.from_bytes(data[:4], 'big')
        if array_len == 0:
            return "[]"

        # Get element type
        elem_type = lv_type.element_type
        if not elem_type:
            return None

        elements = []
        idx = 4

        for _ in range(array_len):
            if idx >= len(data):
                break

            elem_val, bytes_consumed = _decode_element(data[idx:], elem_type)
            if elem_val is None:
                return None

            elements.append(elem_val)
            idx += bytes_consumed

        return "[" + ", ".join(elements) + "]"
    except (ValueError, IndexError):
        return None


def _decode_cluster_default(data: bytes, lv_type: LVType) -> str | None:
    """Decode a cluster default value from DefaultData bytes.

    Format: sequential fields encoded by their respective types
    """
    if not lv_type.fields:
        return None

    try:
        field_values = {}
        idx = 0

        for field in lv_type.fields:
            if idx >= len(data):
                break

            field_val, bytes_consumed = _decode_element(data[idx:], field.type)
            if field_val is None:
                # Use type default for this field
                field_val = get_default_for_type(field.type)

            field_values[field.name] = field_val
            idx += bytes_consumed

        # Format as dict literal
        items = [f"'{k}': {v}" for k, v in field_values.items()]
        return "{" + ", ".join(items) + "}"
    except (ValueError, IndexError):
        return None


def _decode_element(data: bytes, elem_type: LVType | None) -> tuple[str | None, int]:
    """Decode a single element and return (value, bytes_consumed).

    Handles all LabVIEW types recursively: primitives, enums,
    arrays (with element_type), and clusters (with fields).

    Args:
        data: Bytes starting at this element
        elem_type: Type of the element

    Returns:
        Tuple of (decoded value string, number of bytes consumed)
    """
    if not elem_type or len(data) == 0:
        return None, 0

    underlying = elem_type.underlying_type or ""
    kind = elem_type.kind

    # String (and Tag, which is string-encoded): 4-byte length prefix + data
    if underlying in ("String", "Tag"):
        if len(data) < 4:
            return None, 0
        str_len = int.from_bytes(data[:4], 'big')
        if len(data) < 4 + str_len:
            return None, 0
        string_val = data[4:4 + str_len].decode('latin-1', errors='replace')
        escaped = string_val.replace('\\', '\\\\').replace("'", "\\'")
        return f"'{escaped}'", 4 + str_len

    # Boolean: 1 byte in binary data
    if underlying == "Boolean":
        return ("True" if data[0] else "False"), 1

    # Enum: decode as its underlying integer type
    if kind == "enum":
        size = _get_numeric_size(underlying)
        if len(data) < size:
            return None, 0
        val = int.from_bytes(data[:size], 'big')
        return str(val), size

    # Numeric integer types
    if underlying.startswith("NumInt") or underlying.startswith("NumUInt"):
        size = _get_numeric_size(underlying)
        if len(data) < size:
            return None, 0
        signed = underlying.startswith("NumInt")
        val = int.from_bytes(data[:size], 'big', signed=signed)
        return str(val), size

    # Float and complex types
    if underlying.startswith("NumFloat") or underlying.startswith("NumComplex"):
        if underlying in ("NumFloat32", "NumComplex64"):
            if len(data) < 4:
                return None, 0
            val = struct.unpack('>f', data[:4])[0]
            return str(val), 4
        else:  # NumFloat64, NumFloatExt, NumComplex128, NumComplexExt
            if len(data) < 8:
                return None, 0
            val = struct.unpack('>d', data[:8])[0]
            return str(val), 8

    # Path: PTH0 prefix
    if underlying == "Path":
        if data.startswith(b'PTH0'):
            path_val = _decode_path_default(data)
            idx = 12
            while idx < len(data):
                seg_len = data[idx]
                if seg_len == 0:
                    idx += 1
                    break
                idx += 1 + seg_len
            return path_val or 'Path("")', idx
        return 'Path("")', 0

    # Array: 4-byte length + elements
    if kind == "array" and elem_type.element_type:
        if len(data) < 4:
            return None, 0
        array_len = int.from_bytes(data[:4], 'big')
        idx = 4
        elements = []
        for _ in range(array_len):
            if idx >= len(data):
                break
            elem_val, consumed = _decode_element(
                data[idx:], elem_type.element_type,
            )
            if elem_val is None:
                break
            elements.append(elem_val)
            idx += consumed
        return "[" + ", ".join(elements) + "]", idx

    # Cluster: sequential fields
    if kind == "cluster" and elem_type.fields:
        idx = 0
        field_values = {}
        for field in elem_type.fields:
            if idx >= len(data):
                break
            field_val, consumed = _decode_element(
                data[idx:], field.type,
            )
            if field_val is None:
                field_values[field.name] = "None"
            else:
                field_values[field.name] = field_val
            idx += consumed
        items = [f"'{k}': {v}" for k, v in field_values.items()]
        return "{" + ", ".join(items) + "}", idx

    # Refnum: 4 bytes (opaque handle)
    if underlying == "Refnum":
        size = min(4, len(data))
        val = int.from_bytes(data[:size], 'big')
        return f"Refnum({val})" if val else "None", size

    # LVVariant: opaque — just report the byte count
    if underlying in ("LVVariant", "Variant"):
        return "Variant()", len(data)

    # MeasureData (timestamp): 16 bytes (8 int + 8 frac)
    if underlying == "MeasureData":
        if len(data) >= 16:
            secs = int.from_bytes(data[:8], 'big', signed=True)
            return f"Timestamp({secs})", 16
        return "Timestamp(0)", len(data)

    return None, 0


def _get_numeric_size(type_name: str) -> int:
    """Get byte size for a numeric type name."""
    if "8" in type_name:
        return 1
    elif "16" in type_name:
        return 2
    elif "32" in type_name:
        return 4
    elif "64" in type_name:
        return 8
    return 4  # Default


