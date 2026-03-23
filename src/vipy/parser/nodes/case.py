"""Case structure parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from vipy.constants import TERMINAL_CLASS
from vipy.graph_types import Tunnel

from ..models import CaseFrame, CaseStructure
from .base import extract_tunnel_mapping

# Tunnel DCO classes used in case structures
CASE_TUNNEL_CLASSES = ("csTun",)  # Case structure tunnel

# Selector DCO classes
SELECTOR_DCO_CLASSES = ("cSelDCO", "caseSel")

# All tunnel DCO classes
ALL_TUNNEL_CLASSES = ("csTun", "selTun", "commentTun")


def extract_case_structures(root: ET.Element) -> list[CaseStructure]:
    """Extract case structures with frame mappings.

    Handles both class='caseStruct' and class='select' elements.

    Case structures in LabVIEW have:
    - A selector terminal that receives the value to switch on
    - Multiple diagram frames (cases), each with its own set of operations
    - Input/output tunnels that connect outer terminals to each frame's inner
      terminals

    Args:
        root: XML root element

    Returns:
        List of CaseStructure with frame mappings
    """
    case_structures: list[CaseStructure] = []

    # Find caseStruct and select elements
    case_elems = list(root.findall(".//*[@class='caseStruct']"))
    case_elems.extend(root.findall(".//*[@class='select']"))

    for case_elem in case_elems:
        case_uid = case_elem.get("uid")
        if not case_uid:
            continue

        cs = _extract_one_case_structure(case_elem, case_uid)
        if cs:
            case_structures.append(cs)

    return case_structures


def _extract_one_case_structure(
    case_elem: ET.Element,
    case_uid: str,
) -> CaseStructure | None:
    """Extract a single case structure from an XML element."""
    selector_terminal_uid: str | None = None
    selector_type: str | None = None
    frames: list[CaseFrame] = []
    tunnels: list[Tunnel] = []

    # Count frames first (needed for selTun expansion)
    diag_list = case_elem.find("diagramList")
    num_frames = 0
    if diag_list is not None:
        num_frames = len(
            diag_list.findall("SL__arrayElement[@class='diag']")
        )

    # Find selector terminal and tunnels
    term_list_elem = case_elem.find("termList")
    if term_list_elem is not None:
        for term_elem in term_list_elem.findall(
            f"SL__arrayElement[@class='{TERMINAL_CLASS}']"
        ):
            term_uid = term_elem.get("uid")

            # Check for selector DCO
            if term_uid and selector_terminal_uid is None:
                for sel_cls in SELECTOR_DCO_CLASSES:
                    dco = term_elem.find(f"dco[@class='{sel_cls}']")
                    if dco is not None:
                        selector_terminal_uid = term_uid
                        selector_type = _infer_selector_type(dco)
                        break

            # Check for tunnel DCO
            dco = term_elem.find("dco")
            if dco is not None:
                dco_class = dco.get("class", "")
                if dco_class in ALL_TUNNEL_CLASSES:
                    new_tunnels = _extract_case_tunnels(
                        dco, dco_class, term_uid, num_frames,
                    )
                    tunnels.extend(new_tunnels)

    # Extract caseSel tunnels from nested structures.
    # These route shift register values across the case boundary
    # to sRN nodes inside the case frames.
    # caseSel termList: [...inner_per_frame..., outer_structural]
    for case_sel in case_elem.findall(".//*[@class='caseSel']"):
        cs_tl = case_sel.find("termList")
        if cs_tl is not None:
            term_refs = [
                e.get("uid")
                for e in cs_tl.findall("SL__arrayElement")
                if e.get("uid")
            ]
            if len(term_refs) >= 2:
                outer_uid = term_refs[-1]
                for inner_uid in term_refs[:-1]:
                    tunnels.append(Tunnel(
                        outer_terminal_uid=outer_uid,
                        inner_terminal_uid=inner_uid,
                        tunnel_type="caseSel",
                    ))

    # Extract commentTun tunnels from comment nodes (annotations).
    # commentTun passes data through transparently — same layout as selTun.
    for comment_tun in case_elem.findall(".//*[@class='commentTun']"):
        ct_tl = comment_tun.find("termList")
        if ct_tl is not None:
            term_refs = [
                e.get("uid")
                for e in ct_tl.findall("SL__arrayElement")
                if e.get("uid")
            ]
            if len(term_refs) >= 2:
                outer_uid = term_refs[-1]
                for inner_uid in term_refs[:-1]:
                    tunnels.append(Tunnel(
                        outer_terminal_uid=outer_uid,
                        inner_terminal_uid=inner_uid,
                        tunnel_type="commentTun",
                    ))

    # Extract diagram frames (cases)
    if diag_list is not None:
        for idx, diag_elem in enumerate(
            diag_list.findall("SL__arrayElement[@class='diag']")
        ):
            frame = _extract_frame(diag_elem, idx)
            if frame:
                frames.append(frame)

    # Infer selector type if not determined
    if not selector_type and frames:
        selector_values = [str(f.selector_value) for f in frames]
        if set(selector_values) <= {
            "True", "False", "Default", "true", "false", "default",
        }:
            selector_type = "boolean"
        elif all(
            v.isdigit() or v == "Default" for v in selector_values
        ):
            selector_type = "integer"
        else:
            selector_type = "string"

    return CaseStructure(
        uid=case_uid,
        selector_terminal_uid=selector_terminal_uid,
        selector_type=selector_type,
        frames=frames,
        tunnels=tunnels,
    )


def _extract_case_tunnels(
    dco: ET.Element,
    dco_class: str,
    outer_terminal_uid: str | None,
    num_frames: int,
) -> list[Tunnel]:
    """Extract tunnel(s) from a case structure DCO.

    For csTun: simple [inner, outer] layout → 1 Tunnel.
    For selTun: per-frame layout [frame0_inner, frame1_inner, ..., outer]
    → one Tunnel per frame.
    """
    if dco_class == "csTun":
        tunnel = extract_tunnel_mapping(dco, dco_class)
        return [tunnel] if tunnel else []

    # selTun: per-frame inner terminals
    dco_term_list = dco.find("termList")
    if dco_term_list is None:
        return []

    term_refs = [
        e.get("uid")
        for e in dco_term_list.findall("SL__arrayElement")
        if e.get("uid")
    ]

    # Layout: [frame0_inner, frame1_inner, ..., outer_self]
    # Last ref is the outer terminal (same as the parent terminal UID)
    if len(term_refs) < 2:
        return []

    outer_uid = term_refs[-1]  # Last is outer
    inner_refs = term_refs[:-1]  # Rest are per-frame inners

    tunnels = []
    for inner_uid in inner_refs:
        tunnels.append(Tunnel(
            outer_terminal_uid=outer_uid,
            inner_terminal_uid=inner_uid,
            tunnel_type=dco_class,
        ))

    return tunnels


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

    # Find operations inside this diagram (direct children only,
    # not recursing into nested structures like inner case/loop nodeList)
    inner_node_uids: list[str] = []
    node_list = diag_elem.find("nodeList")
    if node_list is not None:
        for node_elem in node_list.findall("SL__arrayElement"):
            node_uid = node_elem.get("uid")
            if node_uid:
                inner_node_uids.append(node_uid)

    return CaseFrame(
        selector_value=selector_value,
        inner_node_uids=inner_node_uids,
        is_default=is_default,
    )


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
