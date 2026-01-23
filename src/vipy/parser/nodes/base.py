"""Base extraction helpers for node parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from vipy.constants import TERMINAL_CLASS
from vipy.graph_types import Tunnel


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

    for term in elem.findall(f".//termList/SL__arrayElement[@class='{TERMINAL_CLASS}']"):
        type_desc = term.find(".//typeDesc")
        obj_flags = term.find("objFlags")

        type_str = type_desc.text if type_desc is not None and type_desc.text else None
        if type_str:
            flags = int(obj_flags.text) if obj_flags is not None and obj_flags.text else 0
            # Bit 0 (isIndicator) = output, bit 0 clear = input
            if flags & 0x1:
                output_types.append(type_str)
            else:
                input_types.append(type_str)

    return input_types, output_types


def extract_label(elem: ET.Element) -> str | None:
    """Extract label text from a node element.

    Args:
        elem: Node element with label child

    Returns:
        Label text or None
    """
    label = elem.find("label/textRec/text")
    if label is not None and label.text:
        return label.text.strip('"')
    return None


def extract_tunnel_mapping(dco: ET.Element, dco_class: str) -> Tunnel | None:
    """Extract tunnel mapping from a dco element.

    Tunnels connect outer terminals to inner terminals across structure boundaries.
    Used by both loops (lSR, rSR, lpTun, lMax) and case structures (csTun).

    Args:
        dco: dco element with tunnel info
        dco_class: Class of the dco (e.g., lSR, rSR, lpTun, lMax, csTun)

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

    # Format is [inner_uid, outer_uid]
    if len(term_refs) >= 2:
        inner_uid = term_refs[0]
        outer_uid = term_refs[1]
        return Tunnel(
            outer_terminal_uid=outer_uid,
            inner_terminal_uid=inner_uid,
            tunnel_type=dco_class,
        )

    return None
