"""Parser utility functions for XML handling."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# Precompiled pattern for XML-encoded control characters (&#xNN;)
_XML_CONTROL_ENTITY_RE = re.compile(r"&#x[0-9a-fA-F]{2};")

# Translation table: strip all control chars except tab (0x09) and newline (0x0A)
_CONTROL_CHAR_TABLE = {c: None for c in range(0x20) if c not in (0x09, 0x0A)}


def clean_labview_string(s: str | None) -> str:
    """Clean a LabVIEW string extracted from XML.

    Handles all common LabVIEW string encoding artifacts:
    - XML-encoded control characters (&#x01;, &#x0D;, etc.)
    - Raw control characters (\\x01, \\x0D, etc.)
    - Surrounding double quotes from label text
    - Null characters (both &#x00; and \\x00)

    Args:
        s: Raw string from XML element, or None

    Returns:
        Cleaned string, or empty string if input is None/empty
    """
    if not s:
        return ""
    s = _XML_CONTROL_ENTITY_RE.sub("", s)
    s = s.translate(_CONTROL_CHAR_TABLE)
    s = s.strip('"')
    return s


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
    # Search paths in priority order
    search_paths = [
        # partID=16 is the user-visible control label
        (".//*[@class='label'][partID='16']/.//text", True),
        # Any label with valid text (older formats)
        (".//*[@class='label']/.//text", True),
        # Direct textRec/text
        (".//textRec/text", True),
        # label/textRec/text (some node types)
        ("label/textRec/text", False),
    ]

    for xpath, filter_pane in search_paths:
        for text_elem in elem.findall(xpath):
            if text_elem is not None and text_elem.text:
                text = clean_labview_string(text_elem.text)
                if not text:
                    continue
                if filter_pane and text.lower() == "pane":
                    continue
                return text

    return None
