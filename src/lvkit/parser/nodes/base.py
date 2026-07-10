"""Base extraction helpers for node parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import Tunnel

from ..constants import TERMINAL_CLASS
from ..flags import is_output_terminal
from ..utils import extract_label, safe_int, safe_text

# Re-export extract_label for backward compatibility
__all__ = ["extract_label", "extract_terminal_types", "extract_tunnel_mapping"]


def extract_terminal_types(
    elem: ET.Element,
) -> tuple[list[str], list[str]]:
    """Extract input and output type descriptors from a node element.

    Args:
        elem: Node element with termList

    Returns:
        Tuple of (input_types, output_types) lists
    """
    input_types: list[str] = []
    output_types: list[str] = []

    selector = f".//termList/SL__arrayElement[@class='{TERMINAL_CLASS}']"
    for term in elem.findall(selector):
        type_desc = term.find(".//typeDesc")
        obj_flags = term.find("objFlags")

        type_str = safe_text(type_desc)
        if type_str:
            flags = safe_int(obj_flags)
            if is_output_terminal(flags):
                output_types.append(type_str)
            else:
                input_types.append(type_str)

    return input_types, output_types


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
