"""The VI's font table (``vi.xml`` ``FTAB``) and the font a text's first run uses.

A text's ``textRec/fr/SL__arrayElement[class=fontRun]`` has ``fontid``, an index
(from 0; absent means 0) into the table. Each ``Font`` row has ``Prop2`` to
``Prop8`` and a ``Name``. Measured on the corpus (``labview-binary-format.md``
"Front panel structure"), only these are settled:

- ``Prop2`` is the size in points; ``32768`` (0x8000) means "the default size".
- ``Prop4`` is ``1000`` for a bold font (its ``Prop7`` metric is larger than the
  same font's non-bold row in 118 of 123 pairs), ``0`` or ``32768`` otherwise.
- ``Name`` is a font name, or a digit: the number of a predefined font
  (0 Application, 1 System, 2 Dialog) the row refers to.
- The first three rows are the three predefined fonts, in that order.

LabVIEW draws the Dialog font bold on Windows, so a predefined font 2 is bold.
Italic, underline and the rest of ``Prop3`` are not read.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence

from .models import ParsedFPFont, ParsedFPFontRun
from .utils import heap_color, int_or_none

_DEFAULT_SIZE = 0x8000
_BOLD_WEIGHT = 1000
_PREDEFINED_COUNT = 3
_DIALOG_FONT = 2


def read_font_table(main_root: ET.Element) -> tuple[ParsedFPFont, ...]:
    """The font table of the VI's ``vi.xml`` root; empty when it has none."""
    section = main_root.find(".//FTAB/Section")
    if section is None:
        return ()
    table = []
    for index, row in enumerate(section.findall("Font")):
        name = (row.findtext("Name") or "").strip()
        if name.isdigit():
            predefined: int | None = int(name)
            name = ""
        else:
            predefined = index if index < _PREDEFINED_COUNT else None
        size = _int(row.get("Prop2"))
        table.append(
            ParsedFPFont(
                name=name or None,
                predefined=predefined,
                size=None if size in (None, _DEFAULT_SIZE) else size,
                bold=_int(row.get("Prop4")) == _BOLD_WEIGHT
                or predefined == _DIALOG_FONT,
            )
        )
    return tuple(table)


def font_of_run(
    table: Sequence[ParsedFPFont], run: ParsedFPFontRun
) -> ParsedFPFont | None:
    """The font ``run`` uses (row 0 when it stores no id); None when the table
    has no such row."""
    index = run.font_id or 0
    return table[index] if 0 <= index < len(table) else None


def font_of_text(
    text_rec: ET.Element | None, table: Sequence[ParsedFPFont]
) -> ParsedFPFont | None:
    """The font of a ``textRec``'s first font run (a text with several runs is
    drawn in its first run's font); None for a text with no run."""
    if text_rec is None:
        return None
    run = text_rec.find("fr/SL__arrayElement")
    if run is None:
        return None
    return font_of_run(table, read_font_run(run))


def read_font_run(run: ET.Element) -> ParsedFPFontRun:
    """One ``fontRun`` element: its ``fontofst`` (hex, absent = 0), ``fontid``
    and ``fontcolor``."""
    offset_text = (run.findtext("fontofst") or "").strip()
    return ParsedFPFontRun(
        offset=int(offset_text, 16) if offset_text else 0,
        font_id=int_or_none(run.findtext("fontid")),
        color=heap_color(run.findtext("fontcolor")),
    )


def _int(text: str | None) -> int | None:
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None
