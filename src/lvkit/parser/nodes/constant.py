"""ParsedConstant value parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..constants import CONSTANT_DCO_CLASS, TERMINAL_CLASS
from ..models import ParsedConstant
from ..utils import (
    clean_labview_string,
    decode_xml_entities_to_bytes,
)
from ..utils import (
    strip_surrounding_quotes as _strip_quotes,
)


def _extract_const_value_hex(dco: ET.Element) -> str | None:
    """The constant's value, as a hex string (bytes.fromhex-compatible).

    Two DCO field names have been observed to carry this:
    - ``ConstValue`` — literal hex text directly (seen in unit-test
      fixtures / older exports).
    - ``DefaultData`` — the field pylabview actually emits for
      ``bDConstDCO`` in every real-world VI checked this session. It is a
      quoted string mixing literal printable bytes and ``&#xNN;``/``&#N;``
      entities for non-printable ones, which must be entity-decoded (NOT
      run through ``clean_labview_string``, which deletes those entities
      rather than decoding them) before being hex-encoded.
    """
    const_val = dco.find("ConstValue")
    if const_val is not None and const_val.text:
        return const_val.text

    default_data = dco.find("DefaultData")
    if default_data is not None and default_data.text:
        raw = _strip_quotes(default_data.text)
        return decode_xml_entities_to_bytes(raw).hex()

    return None


def _extract_display_format(dco: ET.Element) -> str | None:
    """Raw printf-style format string from the constant's own numeric-label
    part (``ddo/partsList/.../numLabel/format``), e.g. ``%.0x`` for hex.

    Scoped to the constant's OWN ``ddo`` (one level: ``ddo/partsList``) —
    NOT a recursive ``.//`` search — so a cluster constant's outer ``dco``
    never picks up a nested field's numeric format (a cluster's own ddo is
    ``stdClust`` and has no ``numLabel`` part; its fields' formats live
    several levels deeper, under ``paneHierarchy/zPlaneList``, and are not
    extracted here)."""
    fmt = dco.find("ddo/partsList/SL__arrayElement[@class='numLabel']/format")
    if fmt is not None and fmt.text:
        text = _strip_quotes(fmt.text)
        return text or None
    return None


def _extract_caption(dco: ET.Element) -> str | None:
    """The constant's VISIBLE developer-authored CAPTION (free-label text), or None.

    LabVIEW keeps a constant's caption in its own ``ddo`` as a ``partsList``
    part ``class="label"`` (NOT a ``<label>`` tag — the earlier selector looked
    for the wrong element and always missed it, so every caption was dropped).

    The label part's ``objFlags`` bit ``0x8`` is its HIDDEN flag and IS honored:
    when you "Create Constant" from a terminal, LabVIEW auto-labels the constant
    with the terminal's name + default (``offset (0)``, ``length (rest)``,
    ``Substring``, ...) and hides it (0x8 set). Only a caption the developer made
    visible (0x8 clear — e.g. ``largeNamesDict``) is shown. (An earlier note here
    had the bit backwards; the corpus is unambiguous: 0x8 set = hidden.)

    The text is quoted and may span multiple lines; ``clean_labview_string``
    strips the quotes and drops binary ``&#xNN;`` byte-entities (so a caption
    that is nothing but a control char, e.g. ``"&#x00;"``, cleans to empty and
    is filtered out).
    """
    label_el = dco.find("ddo/partsList/SL__arrayElement[@class='label']")
    if label_el is None:
        return None
    try:
        flags = int((label_el.findtext("objFlags") or "0").strip())
    except ValueError:
        flags = 0
    if flags & 0x8:
        return None  # hidden auto-label (terminal name + default) — LabVIEW hides it
    text_el = label_el.find("textRec/text")
    if text_el is None or not text_el.text:
        return None
    caption = clean_labview_string(text_el.text).strip()
    return caption or None


def extract_constants(root: ET.Element) -> list[ParsedConstant]:
    """Extract constants from the block diagram.

    Args:
        root: XML root element

    Returns:
        List of ParsedConstant values
    """
    constants = []

    selector = f".//nodeList//SL__arrayElement[@class='{TERMINAL_CLASS}']"
    for term in root.findall(selector):
        dco = term.find(f"dco[@class='{CONSTANT_DCO_CLASS}']")
        if dco is None:
            continue

        uid = term.get("uid")
        type_desc = dco.find("typeDesc")
        value_hex = _extract_const_value_hex(dco)
        label = _extract_caption(dco)

        if value_hex is not None:
            constants.append(
                ParsedConstant(
                    uid=uid or "",
                    type_desc=(
                        (type_desc.text or "unknown")
                        if type_desc is not None
                        else "unknown"
                    ),
                    value=value_hex,
                    label=label,
                    display_format=_extract_display_format(dco),
                )
            )

    return constants
