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

    Searches for label text in multiple locations:
    1. Elements with class='label' containing text
    2. Direct textRec/text children

    Filters out empty strings and "pane" labels.

    Args:
        elem: XML element to search

    Returns:
        Label text or None if not found
    """
    # Try class='label' elements first (more specific)
    for part in elem.findall(".//*[@class='label']"):
        text_elem = part.find(".//text")
        if text_elem is not None and text_elem.text:
            text = text_elem.text.strip('"')
            if text.lower() not in ("pane", ""):
                return text

    # Fall back to direct textRec/text
    text_elem = elem.find(".//textRec/text")
    if text_elem is not None and text_elem.text:
        text = text_elem.text.strip('"')
        if text.lower() not in ("pane", ""):
            return text

    # Also try label/textRec/text (used by some node types)
    label = elem.find("label/textRec/text")
    if label is not None and label.text:
        return label.text.strip('"')

    return None
