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


def strip_surrounding_quotes(text: str) -> str:
    """Strip ONE layer of surrounding double quotes, if present.

    For ``DefaultData``/``ConstValue`` values that must then be passed to
    :func:`decode_xml_entities_to_bytes`: it removes only the wrapping quotes,
    leaving embedded ``&#xNN;`` byte-entities intact (unlike
    :func:`clean_labview_string`, which DELETES those entities and so destroys
    every non-printable byte — including the null bytes in a string/numeric/
    path default's length prefix)."""
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    return text


def decode_xml_entities_to_bytes(data: str) -> bytes:
    """Convert a LabVIEW ``DefaultData``/``ConstValue``-style string to raw
    bytes. The value is emitted as a quoted string where printable bytes are
    literal characters and non-printable bytes are ``&#xNN;``/``&#N;``
    entities (already un-escaped once by ElementTree, so ``&amp;#x00;`` in
    the source XML arrives here as the literal text ``&#x00;``).

    NOTE: do not run this string through ``clean_labview_string`` first —
    that function DELETES ``&#xNN;`` sequences (it is meant for display
    text), which would silently drop every encoded byte before this
    function ever sees it.
    """
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i:i + 3] == "&#x":
            end = data.find(";", i)
            if end != -1:
                hex_val = data[i + 3:end]
                result.append(int(hex_val, 16))
                i = end + 1
                continue
        elif data[i:i + 2] == "&#":
            end = data.find(";", i)
            if end != -1:
                dec_val = data[i + 2:end]
                result.append(int(dec_val))
                i = end + 1
                continue
        result.append(ord(data[i]) & 0xFF)
        i += 1
    return bytes(result)


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
    # Object-scoped, grounded in LabVIEW's heap layout: an object's OWN label
    # lives among its own parts -- inside its <partsList> (the grouping of a
    # control's cosmetic parts + label), or, for node types that don't use
    # partsList, as a DIRECT <label> child. A nested object (a cluster field, a
    # loop frame's subVI) keeps its parts under ITSELF, outside this object's
    # partsList -- so we never steal its label, which is the descendant grab
    # that used to force per-caller guards. partID=16 is the user-visible label;
    # prefer it, then any own label, then a bare textRec / Formula-Node name.
    for xpath in (
        "./partsList/*[@class='label'][partID='16']",
        "./label[partID='16']",
        "./partsList/*[@class='label']",
        "./label",
    ):
        for label in elem.findall(xpath):
            text = _first_text(label)
            if text and text.lower() != "pane":
                return text
    for xpath in ("./textRec/text", "./vblName/text"):
        el = elem.find(xpath)
        if el is not None and el.text:
            text = clean_labview_string(el.text)
            if text and text.lower() != "pane":
                return text
    return None


def extract_caption(elem: ET.Element) -> str | None:
    """Extract caption text from an XML element.

    Searches for caption text in the proper location:
    - partID=82 is the user-visible caption in LabVIEW -- a display-only
      alias distinct from the label (partID=16, see extract_label).

    Verified clean-room: pylabview's ``LVparts.PARTID`` enum defines
    ``CAPTION = 82`` (``NAME_LABEL = 16``; partID 17 is unrelated --
    ``SCALE``, a numeric/graph axis part, not a label at all). Confirmed
    against real extracted front-panel XML: a control's own ``partsList``
    carries sibling ``class='label'`` parts for partID 16 (label / code
    identity, e.g. ``"pin_name"``) and partID 82 (caption / display alias,
    e.g. ``"Pin Name"``) with independently-settable text -- when the
    caption hasn't been customized it mirrors the label's text; when unset
    entirely it's a lone null byte.

    Object-scoped like extract_label: only this element's OWN partID=82
    part is read, never a descendant's.

    Args:
        elem: XML element to search

    Returns:
        Caption text or None if not found/unset
    """
    for xpath in (
        "./partsList/*[@class='label'][partID='82']",
        "./label[partID='82']",
    ):
        for label in elem.findall(xpath):
            text = _first_text(label)
            if text and text.lower() != "pane":
                return text
    return None


def _first_text(label: ET.Element) -> str | None:
    """First non-empty cleaned text anywhere inside a label's own subtree."""
    for t in label.iter("text"):
        if t.text:
            cleaned = clean_labview_string(t.text)
            if cleaned:
                return cleaned
    return None
