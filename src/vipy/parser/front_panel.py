"""Front panel parsing - connector pane, controls, indicators."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .models import ConnectorPane, ConnectorPaneSlot, FPTerminal

from vipy.constants import FP_TERMINAL_CLASS


def extract_fp_terminals(root: ET.Element) -> list[FPTerminal]:
    """Extract front panel terminals (VI inputs and outputs) from block diagram.

    In LabVIEW, fPTerm elements on the block diagram represent connections to
    front panel controls (inputs) and indicators (outputs).

    We determine input vs output by analyzing signal (wire) directions:
    - If wires flow TO the fPTerm, it's an output (indicator)
    - If wires flow FROM the fPTerm, it's an input (control)

    Args:
        root: XML root element

    Returns:
        List of FPTerminal
    """
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
        name = label_elem.text.strip('"') if label_elem is not None and label_elem.text else None

        fp_term_data[uid] = {
            "fp_dco_uid": fp_dco_uid or "",
            "name": name,
            "is_indicator": False,
        }

    # Analyze signals to determine input vs output
    for sig in root.findall(".//signalList/SL__arrayElement[@class='signal']"):
        terms = [t.get("uid") for t in sig.findall("termList/SL__arrayElement")]
        if len(terms) >= 2:
            destinations = terms[1:]
            for dest in destinations:
                if dest in fp_term_uids:
                    fp_term_data[dest]["is_indicator"] = True

    # Build the result list
    terminals = []
    for uid, data in fp_term_data.items():
        terminals.append(FPTerminal(
            uid=uid,
            fp_dco_uid=data["fp_dco_uid"],
            name=data["name"],
            is_indicator=data["is_indicator"],
        ))

    return terminals


def parse_connector_pane(fp_xml_path: Path | str) -> ConnectorPane | None:
    """Parse the connector pane from a front panel XML file.

    The connector pane defines which front panel controls/indicators
    are exposed as VI terminals and their slot positions.

    Args:
        fp_xml_path: Path to the *_FPHb.xml file

    Returns:
        ConnectorPane with slot assignments, or None if not found
    """
    tree = ET.parse(fp_xml_path)
    root = tree.getroot()

    con_pane = root.find(".//conPane[@class='conPane']")
    if con_pane is None:
        return None

    con_id_elem = con_pane.find("conId")
    pattern_id = int(con_id_elem.text) if con_id_elem is not None and con_id_elem.text else 0

    slots: list[ConnectorPaneSlot] = []
    cons = con_pane.find("cons")
    if cons is not None:
        current_index = 0
        for elem in cons.findall("SL__arrayElement[@class='ConpaneConnection']"):
            index_attr = elem.get("index")
            if index_attr is not None:
                current_index = int(index_attr)

            conn_dco = elem.find("ConnectionDCO")
            fp_dco_uid = conn_dco.get("uid") if conn_dco is not None else None

            slots.append(ConnectorPaneSlot(
                index=current_index,
                fp_dco_uid=fp_dco_uid,
            ))

            current_index += 1

    return ConnectorPane(pattern_id=pattern_id, slots=slots)


def parse_connector_pane_types(
    main_xml_path: Path | str,
    fp_conpane: ConnectorPane,
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
        fp_conpane: ConnectorPane from FPHb with connected slot indices

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
            rules[idx] = (flags >> 8) & 0x03

        return rules

    return {}
