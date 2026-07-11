"""Front panel parsing - connector pane, controls, indicators."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from lvkit.models import LVType
from lvkit.parser.utils import clean_labview_string

from .constants import FP_TERMINAL_CLASS
from .flags import get_wiring_rule
from .models import (
    ParsedConnectorPane,
    ParsedConnectorPaneSlot,
    ParsedFPTerminal,
    ParsedType,
)
from .type_resolution import resolve_type_rich


def _lvtype_to_parsed(lv_type: LVType) -> ParsedType:
    """Convert LVType to ParsedType for parser output.

    Parser outputs ParsedType (clean, no external resolution).
    Graph layer enriches to LVType with values/fields from vilib_resolver.
    """
    return ParsedType(
        kind=lv_type.kind,
        type_name=lv_type.underlying_type or "unknown",
        typedef_path=lv_type.typedef_path,
        typedef_name=lv_type.typedef_name,
        ref_type=lv_type.ref_type,
        classname=lv_type.classname,
        fields=lv_type.fields,
        enum_values=lv_type.values,
        element_type=(
            _lvtype_to_parsed(lv_type.element_type)
            if lv_type.element_type else None
        ),
        dimensions=lv_type.dimensions,
    )


# Bit 0 of an fPDCO's ``objFlags`` is LabVIEW's control/indicator designation:
# set => indicator (VI output), clear => control (VI input). It is stored on the
# node itself, so it is AUTHORITATIVE even for an UNWIRED terminal — unlike
# inferring direction from wire connectivity, which cannot classify a terminal
# with no wires (e.g. an error-out indicator left unconnected).
_FP_INDICATOR_FLAG = 0x1


@dataclass(frozen=True)
class FpDcoInfo:
    """The stored attributes of one front-panel DCO node, read straight off the
    node: its type descriptor and its control/indicator designation.
    ``is_indicator`` is None only when the node carries no ``objFlags``."""

    type_desc: str | None = None
    is_indicator: bool | None = None


def extract_fp_dco_info(fp_xml_path: Path | str) -> dict[str, FpDcoInfo]:
    """Read each front-panel DCO node's stored attributes in a single pass,
    keyed by DCO uid: its ``typeDesc`` (the actual LabVIEW type) and its
    control/indicator flag (``objFlags`` bit 0).
    """
    root = ET.parse(fp_xml_path).getroot()

    info: dict[str, FpDcoInfo] = {}
    for dco in root.findall(".//*[@class='fPDCO']"):
        uid = dco.get("uid")
        if not uid:
            continue
        type_desc_elem = dco.find("typeDesc")
        type_desc = (
            type_desc_elem.text
            if type_desc_elem is not None and type_desc_elem.text
            else None
        )
        is_indicator: bool | None = None
        obj_flags = dco.find("objFlags")
        if obj_flags is not None and obj_flags.text:
            text = obj_flags.text.strip()
            if text.lstrip("-").isdigit():
                is_indicator = bool(int(text) & _FP_INDICATOR_FLAG)
        info[uid] = FpDcoInfo(type_desc=type_desc, is_indicator=is_indicator)

    return info


def extract_fp_terminals(
    root: ET.Element,
    fp_xml_path: Path | str | None = None,
    type_map: dict[int, LVType] | None = None,
) -> list[ParsedFPTerminal]:
    """Extract front panel terminals (VI inputs and outputs) from block diagram.

    In LabVIEW, fPTerm elements on the block diagram represent connections to
    front panel controls (inputs) and indicators (outputs).

    We determine input vs output by analyzing signal (wire) directions:
    - If wires flow TO the fPTerm, it's an output (indicator)
    - If wires flow FROM the fPTerm, it's an input (control)

    Args:
        root: XML root element (BD XML)
        fp_xml_path: Optional path to FP XML for extracting typeDesc from DCOs
        type_map: Optional type map for resolving TypeID references to LVType

    Returns:
        List of ParsedFPTerminal with resolved types
    """
    # Read each DCO node's stored attributes (type + control/indicator flag).
    dco_info: dict[str, FpDcoInfo] = {}
    if fp_xml_path:
        dco_info = extract_fp_dco_info(fp_xml_path)

    # First, collect all fPTerm UIDs
    fp_term_uids = set()
    fp_term_data = {}

    for fp_term in root.findall(f".//*[@class='{FP_TERMINAL_CLASS}']"):
        uid = fp_term.get("uid")
        if not uid:
            continue
        fp_term_uids.add(uid)

        dco = fp_term.find("dco")
        fp_dco_uid = dco.get("uid") if dco is not None else None

        label_elem = fp_term.find(".//label/textRec/text")
        name = (
            clean_labview_string(label_elem.text)
            if label_elem is not None and label_elem.text
            else None
        )

        # The DCO node carries both the type and the control/indicator flag.
        info = dco_info.get(fp_dco_uid) if fp_dco_uid else None

        fp_term_data[uid] = {
            "fp_dco_uid": fp_dco_uid or "",
            "name": name,
            # Authoritative flag straight off the node: True/False when stored,
            # None when the node carried no objFlags (~8% of DCOs) — those fall
            # back to the wire-direction inference below.
            "is_indicator": info.is_indicator if info else None,
            "type_desc": info.type_desc if info else None,
        }

    # Fallback for terminals whose DCO node stored no flag: a terminal that
    # RECEIVES a wire is an indicator (output). Only fills a None — it never
    # overrides a stored flag.
    for sig in root.findall(".//signalList/SL__arrayElement[@class='signal']"):
        terms = [t.get("uid") for t in sig.findall("termList/SL__arrayElement")]
        if len(terms) >= 2:
            destinations = terms[1:]
            for dest in destinations:
                if dest in fp_term_uids and fp_term_data[dest]["is_indicator"] is None:
                    fp_term_data[dest]["is_indicator"] = True

    # Build the result list with resolved types
    terminals = []
    for uid, data in fp_term_data.items():
        # Resolve TypeID string to ParsedType
        parsed_type = None
        type_desc_str = data["type_desc"]
        if type_desc_str and type_map:
            lv_type = resolve_type_rich(type_desc_str, type_map)
            parsed_type = _lvtype_to_parsed(lv_type)

        # None here means no stored flag AND no incoming wire => a control.
        is_indicator = bool(data["is_indicator"])

        terminals.append(ParsedFPTerminal(
            uid=uid,
            fp_dco_uid=data["fp_dco_uid"],
            name=data["name"],
            is_indicator=is_indicator,
            parsed_type=parsed_type,
        ))

    return terminals


def parse_connector_pane(fp_xml_path: Path | str) -> ParsedConnectorPane | None:
    """Parse the connector pane from a front panel XML file.

    The connector pane defines which front panel controls/indicators
    are exposed as VI terminals and their slot positions.

    Args:
        fp_xml_path: Path to the *_FPHb.xml file

    Returns:
        ParsedConnectorPane with slot assignments, or None if not found
    """
    tree = ET.parse(fp_xml_path)
    root = tree.getroot()

    con_pane = root.find(".//conPane[@class='conPane']")
    if con_pane is None:
        return None

    con_id_elem = con_pane.find("conId")
    pattern_id = (
        int(con_id_elem.text) if con_id_elem is not None and con_id_elem.text else 0
    )

    slots: list[ParsedConnectorPaneSlot] = []
    cons = con_pane.find("cons")
    if cons is not None:
        current_index = 0
        for elem in cons.findall("SL__arrayElement[@class='ConpaneConnection']"):
            index_attr = elem.get("index")
            if index_attr is not None:
                current_index = int(index_attr)

            conn_dco = elem.find("ConnectionDCO")
            fp_dco_uid = conn_dco.get("uid") if conn_dco is not None else None

            slots.append(ParsedConnectorPaneSlot(
                index=current_index,
                fp_dco_uid=fp_dco_uid,
            ))

            current_index += 1

    return ParsedConnectorPane(pattern_id=pattern_id, slots=slots)


def parse_connector_pane_types(
    main_xml_path: Path | str,
    fp_conpane: ParsedConnectorPane,
) -> dict[int, int]:
    """Get wiring rules for connected connector pane terminals.

    Finds the VI's connector pane Function TypeDesc by matching connected
    slot indices from the FPHb conpane, then extracts wiring rules.

    Wiring rule encoding in TypeDesc Flags bits 8-9:
    - 0 = Invalid Wire Rule
    - 1 = Required
    - 2 = Recommended
    - 3 = Optional
    - 4 = Dynamic Dispatch

    Args:
        main_xml_path: Path to the main .xml file (not BDHb/FPHb)
        fp_conpane: ParsedConnectorPane from FPHb with connected slot indices

    Returns:
        Dict mapping slot index -> wiring rule (0-4)
    """
    connected_indices = {s.index for s in fp_conpane.slots if s.fp_dco_uid}
    if not connected_indices:
        return {}

    max_index = max(connected_indices)

    tree = ET.parse(main_xml_path)
    root = tree.getroot()

    for func_td in root.findall(".//TypeDesc[@Type='Function']"):
        children = func_td.findall("TypeDesc")
        if len(children) <= max_index:
            continue

        matches = all(
            children[i].get("Flags", "0x0000") != "0x0000"
            for i in connected_indices
        )
        if not matches:
            continue

        rules: dict[int, int] = {}
        for idx in connected_indices:
            flags_str = children[idx].get("Flags", "0x0000")
            try:
                flags = int(flags_str, 16)
            except ValueError:
                flags = 0
            rules[idx] = get_wiring_rule(flags)

        return rules

    return {}
