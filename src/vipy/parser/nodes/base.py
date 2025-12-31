"""Base extraction helpers for node parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from vipy.constants import TERMINAL_CLASS


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
