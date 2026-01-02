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

from .models import (
    BlockDiagram,
    Constant,
    FPTerminal,
    Node,
    TerminalInfo,
    Wire,
)
from .nodes import extract_constants, extract_loops
from .nodes.base import extract_label, extract_terminal_types
from .front_panel import extract_fp_terminals


def parse_block_diagram(xml_path: Path | str) -> BlockDiagram:
    """Parse a pylabview block diagram XML file.

    Args:
        xml_path: Path to the *_BDHb.xml file

    Returns:
        BlockDiagram with extracted nodes, constants, and wires
    """
    tree = ET.parse(xml_path)
    root = tree.getroot()

    nodes = _extract_nodes(root)
    constants = extract_constants(root)
    wires = _extract_wires(root)
    fp_terminals = extract_fp_terminals(root)
    enum_labels = _extract_enum_labels(root)
    terminal_info = _extract_terminal_info(root, constants, fp_terminals, wires)
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


def _extract_nodes(root: ET.Element) -> list[Node]:
    """Extract nodes from the block diagram."""
    nodes = []

    for cls in OPERATION_NODE_CLASSES:
        for elem in root.findall(f".//*[@class='{cls}']"):
            uid = elem.get("uid")

            name = extract_label(elem)
            input_types, output_types = extract_terminal_types(elem)

            prim_idx_elem = elem.find("primIndex")
            prim_res_elem = elem.find("primResID")

            node = Node(
                uid=uid,
                node_type=cls,
                name=name,
                prim_index=int(prim_idx_elem.text) if prim_idx_elem is not None else None,
                prim_res_id=int(prim_res_elem.text) if prim_res_elem is not None else None,
                input_types=input_types,
                output_types=output_types,
            )
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
) -> dict[str, TerminalInfo]:
    """Extract detailed terminal info for graph-native representation.

    Captures:
    - Terminal position (index) in parent's termList
    - Input vs output direction (from wire connectivity)
    - Type information

    Args:
        root: XML root element
        constants: List of parsed constants
        fp_terminals: List of front panel terminals
        wires: List of parsed wires (for direction inference)

    Returns:
        Dict mapping terminal UID to TerminalInfo
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

                type_desc_elem = term.find(".//typeDesc")
                type_id = type_desc_elem.text if type_desc_elem is not None else None

                terminal_info[term_uid] = TerminalInfo(
                    uid=term_uid,
                    parent_uid=elem_uid,
                    index=parm_index,
                    is_output=is_output,
                    type_id=type_id,
                )

    # Constants have a single output terminal
    for const in constants:
        if const.uid not in terminal_info:
            terminal_info[const.uid] = TerminalInfo(
                uid=const.uid,
                parent_uid=const.uid,
                index=0,
                is_output=True,
                type_id=const.type_desc,
            )

    # Front panel terminals
    for fp_term in fp_terminals:
        if fp_term.uid not in terminal_info:
            terminal_info[fp_term.uid] = TerminalInfo(
                uid=fp_term.uid,
                parent_uid=fp_term.uid,
                index=0,
                is_output=not fp_term.is_indicator,
                type_id=None,
                name=fp_term.name,
            )

    return terminal_info
