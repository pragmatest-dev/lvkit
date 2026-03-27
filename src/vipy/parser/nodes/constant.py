"""Constant value parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from vipy.constants import CONSTANT_DCO_CLASS, TERMINAL_CLASS

from ..models import Constant
from ..utils import clean_labview_string


def extract_constants(root: ET.Element) -> list[Constant]:
    """Extract constants from the block diagram.

    Args:
        root: XML root element

    Returns:
        List of Constant values
    """
    constants = []

    selector = f".//nodeList//SL__arrayElement[@class='{TERMINAL_CLASS}']"
    for term in root.findall(selector):
        dco = term.find(f"dco[@class='{CONSTANT_DCO_CLASS}']")
        if dco is None:
            continue

        uid = term.get("uid")
        type_desc = dco.find("typeDesc")
        const_val = dco.find("ConstValue")

        # Try to get label from nested ddo
        label_elem = dco.find(".//multiLabel/buf")
        label = label_elem.text if label_elem is not None else None

        # Also check for regular labels
        if label is None:
            label_elem = dco.find(".//label/textRec/text")
            if label_elem is not None and label_elem.text:
                label = clean_labview_string(label_elem.text)

        if const_val is not None:
            constants.append(Constant(
                uid=uid,
                type_desc=type_desc.text if type_desc is not None else "unknown",
                value=const_val.text,
                label=label,
            ))

    return constants
