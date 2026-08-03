"""Base extraction helpers for node parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import Tunnel

from ..constants import NODE_CLASS_COMMENT, STRUCTURE_NODE_CLASSES
from ..utils import extract_label

# Re-export extract_label for backward compatibility
__all__ = [
    "extract_label",
    "extract_tunnel_mapping",
    "frame_inner_node_uids",
]

# Structure classes that LabVIEW may list ONLY in a frame diagram's zPlaneList
# (not its nodeList) — see frame_inner_node_uids.
_FRAME_ZPLANE_STRUCTS = STRUCTURE_NODE_CLASSES | {NODE_CLASS_COMMENT}


def frame_inner_node_uids(diag_elem: ET.Element) -> list[str]:
    """UIDs of the nodes/structures on a frame's diagram.

    Reads ``nodeList`` (the primary list, unchanged behaviour) AND augments it
    with any STRUCTURE element found only in ``zPlaneList``. Neither list is a
    superset: for a NESTED FLAT SEQUENCE, LabVIEW lists the flat seq's inner
    ``sequenceFrame`` in the parent frame's ``nodeList`` but the ``flatSequence``
    structure itself ONLY in the z-plane — so a nodeList-only walk orphans it
    (and everything inside it) to the top level. nodeList also uniquely carries
    the shift registers and zPlaneList uniquely carries decorations, so we
    union: every nodeList member, plus every zPlaneList element whose class is a
    structure. Order preserved, deduped.
    """
    uids: list[str] = []
    seen: set[str] = set()
    node_list = diag_elem.find("nodeList")
    if node_list is not None:
        for e in node_list.findall("SL__arrayElement"):
            u = e.get("uid")
            if u and u not in seen:
                seen.add(u)
                uids.append(u)
    zplane = diag_elem.find("zPlaneList")
    if zplane is not None:
        for e in zplane.findall("SL__arrayElement"):
            u = e.get("uid")
            if u and u not in seen and e.get("class") in _FRAME_ZPLANE_STRUCTS:
                seen.add(u)
                uids.append(u)
    return uids


def extract_tunnel_mapping(dco: ET.Element, dco_class: str) -> list[Tunnel]:
    """Extract tunnel mapping(s) from a dco element.

    Tunnels connect outer terminals to inner terminals across structure
    boundaries. Used by loops (lSR, rSR, lpTun, lMax), case structures (csTun),
    and sequences (seqTun, flatSeqTun).

    The termList is ``[inner_frame0, inner_frame1, ..., outer]``: the OUTER face
    is the LAST element, and every preceding element is a per-frame inner face.
    - A loop / flat-sequence / case-frame boundary tunnel has exactly ONE inner
      (2-element list → one Tunnel — the common case, unchanged).
    - A STACKED-sequence (or any multi-frame) tunnel is a UNION: one outer face
      shared across N frames, so N inner faces (N+1 elements → N Tunnels sharing
      the outer). This shape self-selects on element count — no branch on
      structure type is needed (flat/loop tunnels are 2-element, so they yield a
      single unchanged pair).

    The previous ``[inner, outer]`` read took only ``term_refs[0]``/``[1]``,
    which for a union DCO both dropped every inner past the first AND mistook an
    inner (``term_refs[1]``) for the outer — producing wrong tunnels for stacked
    sequences.

    Args:
        dco: dco element with tunnel info
        dco_class: Class of the dco (e.g., lSR, rSR, lpTun, lMax, csTun, seqTun)

    Returns:
        List of Tunnels (one per inner face); empty if invalid.
    """
    dco_term_list = dco.find("termList")
    if dco_term_list is None:
        return []

    term_refs: list[str] = [
        uid
        for e in dco_term_list.findall("SL__arrayElement")
        if (uid := e.get("uid"))
    ]

    if len(term_refs) < 2:
        return []

    outer_uid = term_refs[-1]
    return [
        Tunnel(
            outer_terminal_uid=outer_uid,
            inner_terminal_uid=inner_uid,
            tunnel_type=dco_class,
        )
        for inner_uid in term_refs[:-1]
    ]
