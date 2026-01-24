"""Parser utility functions for XML handling."""

from __future__ import annotations

import xml.etree.ElementTree as ET


def safe_text(elem: ET.Element | None, default: str = "") -> str:
    """Safely get text content from an XML element.

    Args:
        elem: XML element or None
        default: Default value if element is None or has no text

    Returns:
        Element text or default value
    """
    if elem is not None and elem.text:
        return elem.text
    return default


def safe_int(elem: ET.Element | None, default: int = 0) -> int:
    """Safely get integer from an XML element's text.

    Args:
        elem: XML element or None
        default: Default value if element is None or has no text

    Returns:
        Parsed integer or default value
    """
    if elem is not None and elem.text:
        try:
            return int(elem.text)
        except ValueError:
            return default
    return default


def safe_attr(elem: ET.Element | None, attr: str, default: str = "") -> str:
    """Safely get an attribute from an XML element.

    Args:
        elem: XML element or None
        attr: Attribute name
        default: Default value if element is None or attribute missing

    Returns:
        Attribute value or default
    """
    if elem is not None:
        return elem.get(attr, default)
    return default


def extract_label(elem: ET.Element) -> str | None:
    """Extract label text from an XML element.

    Searches for label text in the proper location:
    - partID=16 is the user-visible control label in LabVIEW
    - Other partIDs (like 82) are internal and may contain null characters

    Filters out empty strings and "pane" labels.

    Args:
        elem: XML element to search

    Returns:
        Label text or None if not found
    """
    # Look for partID=16 labels first - this is the user-visible label
    for part in elem.findall(".//*[@class='label']"):
        part_id_elem = part.find("partID")
        if part_id_elem is not None and part_id_elem.text == "16":
            text_elem = part.find(".//text")
            if text_elem is not None and text_elem.text:
                text = text_elem.text.strip('"')
                if text.lower() not in ("pane", ""):
                    return text

    # Fall back to any label with valid text (for older formats or edge cases)
    for part in elem.findall(".//*[@class='label']"):
        text_elem = part.find(".//text")
        if text_elem is not None and text_elem.text:
            text = text_elem.text.strip('"')
            # Skip null characters and pane labels
            if text.lower() not in ("pane", "") and "&#x00" not in text and "\x00" not in text:
                return text

    # Fall back to direct textRec/text
    text_elem = elem.find(".//textRec/text")
    if text_elem is not None and text_elem.text:
        text = text_elem.text.strip('"')
        if text.lower() not in ("pane", "") and "&#x00" not in text:
            return text

    # Also try label/textRec/text (used by some node types)
    label = elem.find("label/textRec/text")
    if label is not None and label.text:
        text = label.text.strip('"')
        if "&#x00" not in text:
            return text

    return None
