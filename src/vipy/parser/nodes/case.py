"""Case structure parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from vipy.constants import TERMINAL_CLASS
from vipy.graph_types import Tunnel

from ..models import CaseFrame, CaseStructure

# Tunnel DCO classes used in case structures
CASE_TUNNEL_CLASSES = ("csTun",)  # Case structure tunnel


def extract_case_structures(root: ET.Element) -> list[CaseStructure]:
    """Extract case structures with frame mappings.

    Case structures in LabVIEW have:
    - A selector terminal that receives the value to switch on
    - Multiple diagram frames (cases), each with its own set of operations
    - Input/output tunnels that connect outer terminals to each frame's inner terminals

    Args:
        root: XML root element

    Returns:
        List of CaseStructure with frame mappings
    """
    case_structures: list[CaseStructure] = []

    for case_elem in root.findall(".//*[@class='caseStruct']"):
        case_uid = case_elem.get("uid")
        if not case_uid:
            continue

        selector_terminal_uid: str | None = None
        selector_type: str | None = None
        frames: list[CaseFrame] = []
        tunnels: list[Tunnel] = []

        # Find selector terminal (first terminal in termList typically)
        term_list_elem = case_elem.find("termList")
        if term_list_elem is not None:
            for term_elem in term_list_elem.findall(
                f"SL__arrayElement[@class='{TERMINAL_CLASS}']"
            ):
                term_uid = term_elem.get("uid")
                if term_uid and selector_terminal_uid is None:
                    # Check if this is a selector (has cSelDCO)
                    dco = term_elem.find("dco[@class='cSelDCO']")
                    if dco is not None:
                        selector_terminal_uid = term_uid
                        # Try to determine selector type from type info
                        selector_type = _infer_selector_type(dco)

                # Check for tunnel dco inside this terminal
                dco = term_elem.find("dco")
                if dco is not None:
                    dco_class = dco.get("class", "")
                    if dco_class in CASE_TUNNEL_CLASSES or dco_class == "csTun":
                        tunnel = _extract_tunnel_mapping(dco, dco_class)
                        if tunnel:
                            tunnels.append(tunnel)

        # Extract diagram frames (cases)
        diag_list = case_elem.find("diagramList")
        if diag_list is not None:
            for idx, diag_elem in enumerate(
                diag_list.findall("SL__arrayElement[@class='diag']")
            ):
                frame = _extract_frame(diag_elem, idx)
                if frame:
                    frames.append(frame)

        # If we didn't find a selector, mark as boolean (default)
        if not selector_type and frames:
            # Check if frames have boolean-like selector values
            selector_values = [f.selector_value for f in frames]
            if set(selector_values) <= {"True", "False", "Default"}:
                selector_type = "boolean"
            elif all(v.isdigit() or v == "Default" for v in selector_values):
                selector_type = "integer"
            else:
                selector_type = "string"

        case_structures.append(CaseStructure(
            uid=case_uid,
            selector_terminal_uid=selector_terminal_uid,
            selector_type=selector_type,
            frames=frames,
            tunnels=tunnels,
        ))

    return case_structures


def _extract_frame(diag_elem: ET.Element, index: int) -> CaseFrame | None:
    """Extract a single case frame from a diagram element.

    Args:
        diag_elem: Diagram element containing the case operations
        index: Index of the frame in the diagramList

    Returns:
        CaseFrame or None if invalid
    """
    # Get selector value from the diagram element
    # This is typically stored in a 'selStr' attribute or child
    selector_value = diag_elem.get("selStr", "")

    # Try alternate location for selector string
    if not selector_value:
        sel_str_elem = diag_elem.find("selStr")
        if sel_str_elem is not None and sel_str_elem.text:
            selector_value = sel_str_elem.text

    # If still no selector, use index-based default
    if not selector_value:
        if index == 0:
            selector_value = "True"  # Default first case for boolean
        else:
            selector_value = "False"  # Default second case for boolean

    # Determine if this is the default case
    is_default = "Default" in selector_value or selector_value.lower() == "default"

    # Find operations inside this diagram
    inner_node_uids: list[str] = []
    for node_list in diag_elem.findall(".//nodeList"):
        for node_elem in node_list.findall("SL__arrayElement"):
            node_uid = node_elem.get("uid")
            if node_uid:
                inner_node_uids.append(node_uid)

    return CaseFrame(
        selector_value=selector_value,
        inner_node_uids=inner_node_uids,
        is_default=is_default,
    )


def _extract_tunnel_mapping(dco: ET.Element, dco_class: str) -> Tunnel | None:
    """Extract tunnel mapping from a dco element.

    Case structure tunnels connect outer terminals to inner terminals
    across the case boundary.

    Args:
        dco: dco element with tunnel info
        dco_class: Class of the dco (csTun)

    Returns:
        Tunnel or None if invalid
    """
    dco_term_list = dco.find("termList")
    if dco_term_list is None:
        return None

    term_refs = [
        e.get("uid")
        for e in dco_term_list.findall("SL__arrayElement")
        if e.get("uid")
    ]

    # Format is [inner_uid, outer_uid] (same as loops)
    if len(term_refs) >= 2:
        inner_uid = term_refs[0]
        outer_uid = term_refs[1]
        if inner_uid is not None and outer_uid is not None:
            return Tunnel(
                outer_terminal_uid=outer_uid,
                inner_terminal_uid=inner_uid,
                tunnel_type="csTun",
            )

    return None


def _infer_selector_type(dco: ET.Element) -> str | None:
    """Infer the selector type from the cSelDCO element.

    Args:
        dco: The cSelDCO element

    Returns:
        Type string ("boolean", "integer", "enum", "string") or None
    """
    # Check for type hints in the element
    type_elem = dco.find("typeDesc")
    if type_elem is not None:
        type_text = type_elem.text or ""
        type_lower = type_text.lower()

        if "bool" in type_lower:
            return "boolean"
        elif "int" in type_lower or "i32" in type_lower or "u32" in type_lower:
            return "integer"
        elif "enum" in type_lower:
            return "enum"
        elif "string" in type_lower:
            return "string"

    # Default to None (will be inferred from frame values later)
    return None
