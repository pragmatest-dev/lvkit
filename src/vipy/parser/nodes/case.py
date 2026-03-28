"""Case structure parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from vipy.constants import STRUCTURE_NODE_CLASSES, TERMINAL_CLASS
from vipy.graph_types import Tunnel

from ..models import CaseFrame, CaseStructure, TerminalInfo
from .base import extract_tunnel_mapping

# Tunnel DCO classes used in case structures
CASE_TUNNEL_CLASSES = ("csTun",)  # Case structure tunnel

# Selector DCO classes
SELECTOR_DCO_CLASSES = ("cSelDCO", "caseSel")

# All tunnel DCO classes
ALL_TUNNEL_CLASSES = ("csTun", "selTun", "commentTun")


def _find_own_descendants(
    elem: ET.Element,
    class_name: str,
) -> list[ET.Element]:
    """Find elements with class_name, stopping at nested structure boundaries.

    Walks the XML subtree but does NOT recurse into nested structure elements
    (caseStruct, select, forLoop, etc.), so only elements belonging to THIS
    structure are returned.
    """
    results: list[ET.Element] = []

    def _walk(e: ET.Element) -> None:
        for child in e:
            child_class = child.get("class", "")
            if child_class == class_name:
                results.append(child)
            # Stop at nested structure boundaries
            if child_class not in STRUCTURE_NODE_CLASSES:
                _walk(child)

    _walk(elem)
    return results


def extract_case_structures(
    root: ET.Element,
    terminal_info: dict[str, TerminalInfo] | None = None,
) -> list[CaseStructure]:
    """Extract case structures with frame mappings.

    Handles both class='caseStruct' and class='select' elements.

    Args:
        root: XML root element
        terminal_info: Terminal info dict (uid → TerminalInfo) for type lookup

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

        cs = _extract_one_case_structure(case_elem, case_uid, terminal_info)
        if cs:
            case_structures.append(cs)

    return case_structures


def _extract_one_case_structure(
    case_elem: ET.Element,
    case_uid: str,
    terminal_info: dict[str, TerminalInfo] | None = None,
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

    # Extract caseSel tunnels from sRN nodes inside this case's diagrams.
    # These route shift register values across the case boundary.
    # caseSel termList: [...inner_per_frame..., outer_structural]
    # IMPORTANT: use _find_own_descendants to avoid picking up caseSel
    # elements from nested case structures.
    for case_sel in _find_own_descendants(case_elem, "caseSel"):
        cs_tl = case_sel.find("termList")
        if cs_tl is not None:
            term_refs: list[str] = [
                uid
                for e in cs_tl.findall("SL__arrayElement")
                if (uid := e.get("uid"))
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
    for comment_tun in _find_own_descendants(case_elem, "commentTun"):
        ct_tl = comment_tun.find("termList")
        if ct_tl is not None:
            term_refs: list[str] = [
                uid
                for e in ct_tl.findall("SL__arrayElement")
                if (uid := e.get("uid"))
            ]
            if len(term_refs) >= 2:
                outer_uid = term_refs[-1]
                for inner_uid in term_refs[:-1]:
                    tunnels.append(Tunnel(
                        outer_terminal_uid=outer_uid,
                        inner_terminal_uid=inner_uid,
                        tunnel_type="commentTun",
                    ))

    # Resolve selector type from the terminal's actual wire type.
    # This is the source of truth — overrides the DCO-based guess.
    if selector_terminal_uid and terminal_info:
        ti = terminal_info.get(selector_terminal_uid)
        if ti and ti.parsed_type:
            selector_type = _type_name_to_selector_type(
                ti.parsed_type.type_name,
            )

    # Extract selector value mapping from SelectRangeArray32.
    # Maps diagramIdx → integer index.
    selector_values_by_diag: dict[int, int] = {}
    select_range = case_elem.find("SelectRangeArray32")
    if select_range is not None:
        for sr_elem in select_range.findall(
            "SL__arrayElement[@class='SelectorRange']"
        ):
            start = sr_elem.findtext("start")
            diag_idx = sr_elem.findtext("diagramIdx")
            if start is not None and diag_idx is not None:
                selector_values_by_diag[int(diag_idx)] = int(start)

    # For string selectors, the start values in SelectRangeArray32 are
    # indices into SelectStringArray (hex-encoded string labels).
    string_labels: list[str] = []
    if selector_type == "string":
        ssa = case_elem.find("SelectStringArray")
        if ssa is not None:
            for item in ssa.findall("SL__arrayElement"):
                hex_text = item.text or ""
                try:
                    string_labels.append(
                        bytes.fromhex(hex_text).decode("utf-8")
                    )
                except (ValueError, UnicodeDecodeError):
                    string_labels.append(hex_text)

    # Detect default case: SelectDefaultCase holds the hex diagram index
    # of the default frame (FF = no default).
    default_diag_idx: int | None = None
    default_case_elem = case_elem.findtext("SelectDefaultCase")
    if default_case_elem and default_case_elem.upper() != "FF":
        try:
            default_diag_idx = int(default_case_elem, 16)
        except ValueError:
            pass

    # Extract diagram frames (cases)
    if diag_list is not None:
        for idx, diag_elem in enumerate(
            diag_list.findall("SL__arrayElement[@class='diag']")
        ):
            resolved_selector: str | None = None
            if idx in selector_values_by_diag:
                sv = selector_values_by_diag[idx]
                if selector_type == "boolean":
                    resolved_selector = "True" if sv == 1 else "False"
                elif selector_type == "string" and sv < len(string_labels):
                    resolved_selector = string_labels[sv]
                else:
                    # Integer, enum, error — use raw value
                    resolved_selector = str(sv)

            is_default = idx == default_diag_idx

            frame = _extract_frame(
                diag_elem, idx, resolved_selector, is_default,
            )
            if frame:
                frames.append(frame)

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

    term_refs: list[str] = [
        uid
        for e in dco_term_list.findall("SL__arrayElement")
        if (uid := e.get("uid"))
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


def _extract_frame(
    diag_elem: ET.Element,
    index: int,
    selector_value: str | None = None,
    is_default: bool = False,
) -> CaseFrame | None:
    """Extract a single case frame from a diagram element.

    Args:
        diag_elem: Diagram element containing the case operations
        index: Index of the frame in the diagramList
        selector_value: Pre-resolved selector value from SelectRangeArray
        is_default: Whether this frame is the default case

    Returns:
        CaseFrame or None if invalid
    """
    # Use pre-resolved selector value when available
    if not selector_value:
        # Fallback: try diagram element's selStr attribute
        selector_value = diag_elem.get("selStr", "")

    if not selector_value:
        sel_str_elem = diag_elem.find("selStr")
        if sel_str_elem is not None and sel_str_elem.text:
            selector_value = sel_str_elem.text

    # Last resort: index-based default (0=False, 1=True)
    if not selector_value:
        selector_value = "True" if index == 1 else "False"

    if is_default:
        selector_value = "Default"

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


def _type_name_to_selector_type(type_name: str) -> str | None:
    """Map a ParsedType.type_name to a selector type category.

    Args:
        type_name: From TerminalInfo.parsed_type.type_name
            e.g. "Boolean", "String", "NumInt32", "Enum", "Cluster"

    Returns:
        "boolean", "integer", "string", "enum", "error", or None
    """
    tn = type_name.lower()
    if tn == "boolean":
        return "boolean"
    if tn == "string":
        return "string"
    if tn.startswith("num") or tn in ("i32", "u32", "i16", "u16", "i8", "u8"):
        return "integer"
    if "enum" in tn:
        return "enum"
    if tn == "cluster":
        # Error cluster selector
        return "error"
    return None


def _infer_selector_type(dco: ET.Element) -> str | None:
    """Fallback: infer selector type from cSelDCO's typeDesc element.

    Used when terminal_info is not available.
    """
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

    return None
