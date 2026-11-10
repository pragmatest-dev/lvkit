"""The front-panel part reader: a heap object's ``partsList`` as
``ParsedFPPart`` values (bounds, colours, picture ids, states, texts, font),
shared by the control parser and the panel's object tree.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence

from .constants import MULTI_LABEL_CLASS
from .fp_fonts import font_of_text
from .models import ParsedFPFont, ParsedFPPart, ParsedFPPartState
from .utils import heap_color, heap_rect, int_or_none, safe_int


def parse_bounds(bounds_str: str) -> tuple[int, int, int, int]:
    """Parse bounds string like '(0, 0, 100, 200)' to tuple."""
    try:
        clean = bounds_str.strip("()")
        parts = [int(x.strip()) for x in clean.split(",")]
        if len(parts) == 4:
            return tuple(parts)  # type: ignore
    except (ValueError, AttributeError):
        pass
    return (0, 0, 100, 200)


def parse_multi_label_buf(buf_text: str) -> list[str]:
    """The quoted item labels out of a ``multiLabel`` ``<buf>`` text (e.g.
    ``(3)"No Op""Increment""Reset"``) -- shared by ``_extract_enum_labels``
    (every enum/ring in a whole XML tree, keyed by uid) and ``_enum_labels_of``
    (one control's own multiLabel), since both solve the identical "regex the
    quoted items out of this buffer" step."""
    return re.findall(r'"([^"]*)"', buf_text)


def _part_states(part: ET.Element) -> tuple[ParsedFPPartState, ...]:
    """A ``bigMultiCosm`` part's ``SubCosm`` states, in stored order; empty for
    every other part class."""
    return tuple(
        ParsedFPPartState(
            bounds=heap_rect(sub.find("Bounds")) or (0, 0, 0, 0),
            fg_color=heap_color(sub.findtext("FGColor")),
            bg_color=heap_color(sub.findtext("BGColor")),
            image_id=int_or_none(sub.findtext("Image/ImageResID")),
        )
        for sub in part.findall("table/SL__arrayElement[@class='SubCosm']")
    )


def _part_texts(part: ET.Element) -> tuple[str, ...]:
    """A ``multiLabel`` part's texts (its ``buf``), in stored order."""
    if part.get("class") != MULTI_LABEL_CLASS:
        return ()
    return tuple(parse_multi_label_buf(part.findtext("buf") or ""))


def _part_image_ids(
    part: ET.Element, states: tuple[ParsedFPPartState, ...]
) -> tuple[int, ...]:
    """The picture ids a part records, in stored order, whichever of the three
    forms it uses: ``image/ImageResID`` (``cosm``, ``label``, ``numLabel``), one
    ``table`` ``Image`` per state (``multiCosm``), or one ``SubCosm`` ``Image``
    per state (``bigMultiCosm``, already read into ``states``)."""
    if states:
        return tuple(st.image_id for st in states if st.image_id is not None)
    single = int_or_none(part.findtext("image/ImageResID"))
    if single is not None:
        return (single,)
    return tuple(
        image_id
        for sub in part.findall("table/SL__arrayElement[@class='Image']")
        if (image_id := int_or_none(sub.findtext("ImageResID"))) is not None
    )


def parse_fp_parts_list(
    owner: ET.Element, fonts: Sequence[ParsedFPFont] = ()
) -> list[ParsedFPPart]:
    """``owner``'s ``partsList`` entries as parts (each with its own bounds,
    objFlags, and -- recursively -- its own sub-parts). objFlags bit 0x8 =
    hidden, the SAME convention ``layout._field_label_hidden`` reads for a
    cluster field's label part, so a view can tell a DEVELOPER-HIDDEN chrome
    part (e.g. an array's own index display, toggled off per-control) from one
    that's simply absent from this control's shape."""
    parts: list[ParsedFPPart] = []
    parts_list = owner.find("partsList")
    if parts_list is None:
        return parts
    for part in parts_list:
        bounds_elem = part.find("bounds")
        if bounds_elem is None or not bounds_elem.text:
            continue
        pid_elem = part.find("partID")
        pid_text = pid_elem.text if pid_elem is not None else None
        part_id = int(pid_text) if pid_text and pid_text.isdigit() else None
        states = _part_states(part)
        parts.append(
            ParsedFPPart(
                part_id=part_id,
                part_class=part.get("class", ""),
                bounds=parse_bounds(bounds_elem.text),
                parts=parse_fp_parts_list(part, fonts),
                fg_color=heap_color(part.findtext("fgColor")),
                bg_color=heap_color(part.findtext("bgColor")),
                image_ids=_part_image_ids(part, states),
                flags=safe_int(part.find("objFlags")),
                states=states,
                texts=_part_texts(part),
                text_color=heap_color(
                    part.findtext("textRec/fr/SL__arrayElement/fontcolor")
                ),
                font=font_of_text(part.find("textRec"), fonts),
            )
        )
    return parts
