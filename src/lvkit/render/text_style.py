"""The size, weight, family and colour a front-panel text is drawn in, from its
first font run (``ParsedFPPart.font``, ``parser/fp_fonts.py``)."""

from __future__ import annotations

from dataclasses import dataclass

from ..parser.models import ParsedFPFont, ParsedFPPart
from .backend import Backend

# A font's stored size is the height of its text line in pixels: a label's box
# is that size + 2 px tall (``labview-binary-format.md`` "Front panel
# structure"). The SVG font size whose line is that tall, for the predefined
# font's face, is 0.75 of it (15 -> about 11 px glyphs, as measured). Checked
# against docs/images/Measurement UI.png (150%): the first letter of "double array
# in", "double in" and "double out" is 13, 14 and 14 px tall in the screenshot, 14,
# 13 and 15 here; and New measurement client dialog.png (100%): the "A" of "Active
# Project Name" and the "A" of "Available Measurements" are 9 px tall, 8 here. The
# text is 5% wider here than there at the same height (116 vs 125, 73 vs 77, 85 vs
# 89, 108 vs 114, 129 vs 135 px) while the heights agree, so the size stays.
_LINE_HEIGHT_TO_FONT_SIZE = 0.75

# The Application font's stored size (the table's first row in every VI
# measured); a text with no font run is drawn at it.
_DEFAULT_SIZE = 15

# Font size of a front-panel label with no font run: a control's own caption and
# each cluster field's name.
LABEL_SIZE = _DEFAULT_SIZE * _LINE_HEIGHT_TO_FONT_SIZE

# The stack for the three predefined fonts (Application, System, Dialog), and
# the fallback after a stored name.
_PREDEFINED_NAME = "Segoe UI"
PREDEFINED_FAMILY = f"'{_PREDEFINED_NAME}', Tahoma, sans-serif"


@dataclass(frozen=True)
class TextStyle:
    """How to draw one text. ``family`` None is the page's own font; ``color`` is
    a literal colour (``#RRGGBB``), None keeps the theme's."""

    size: float = LABEL_SIZE
    bold: bool = False
    family: str | None = None
    color: str | None = None


def text_style(
    font: ParsedFPFont | None,
    color: str | None = None,
    default_size: float = LABEL_SIZE,
) -> TextStyle:
    """The style of a text whose first run uses ``font`` (None: no run) and has
    the literal colour ``color``. ``default_size`` is the size the caller draws
    a ``_DEFAULT_SIZE`` text at; another size scales it, so a text in the
    default font is drawn as it was measured."""
    if font is None:
        return TextStyle(default_size, color=color)
    size = (
        default_size * font.size / _DEFAULT_SIZE
        if font.size is not None
        else default_size
    )
    family = PREDEFINED_FAMILY
    if font.name and font.name != _PREDEFINED_NAME:
        stored = font.name.replace("'", "").replace('"', "")
        family = f"'{stored}', {PREDEFINED_FAMILY}"
    return TextStyle(size, font.bold, family, color)


def part_style(
    part: ParsedFPPart | None, default_size: float = LABEL_SIZE
) -> TextStyle:
    """The style of ``part``'s text; the default for no part."""
    if part is None:
        return TextStyle(default_size)
    return text_style(part.font, part.text_color, default_size)


def draw_styled_text(
    backend: Backend,
    x: float,
    y: float,
    text: str,
    style: TextStyle,
    fallback_fill: str,
    *,
    anchor: str = "start",
) -> None:
    """``text`` at (``x``, ``y``), in ``style``; the theme's ``fallback_fill``
    when the style has no literal colour."""
    backend.text(
        x,
        y,
        text,
        style.size,
        fill=style.color or fallback_fill,
        bold=style.bold,
        family=style.family,
        anchor=anchor,
    )
