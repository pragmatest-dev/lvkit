"""The size, weight, family and colour a front-panel text is drawn in, from its
first font run (``ParsedFPPart.font``, ``parser/fp_fonts.py``)."""

from __future__ import annotations

from dataclasses import dataclass

from ..parser.models import ParsedFPFont, ParsedFPPart
from .backend import Backend

# LabVIEW sizes are points; the diagram is in pixels at 96 dpi.
_PT_TO_PX = 0.75

# The Application font's size in points (the table's first row in every VI
# measured); a text with no font run is drawn at it.
_DEFAULT_POINTS = 15

# Font size of a front-panel label with no font run: a control's own caption and
# each cluster field's name. 15 points, as the screenshots show it.
LABEL_SIZE = _DEFAULT_POINTS * _PT_TO_PX

# The stack for the three predefined fonts (Application, System, Dialog), and
# the fallback after a stored name.
PREDEFINED_FAMILY = "'Segoe UI', Tahoma, sans-serif"


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
    a ``_DEFAULT_POINTS`` text at; another size scales it, so a text in the
    default font is drawn as it was measured."""
    if font is None:
        return TextStyle(default_size, color=color)
    size = (
        default_size * font.size / _DEFAULT_POINTS
        if font.size is not None
        else default_size
    )
    if font.name:
        family = f"'{font.name.replace(chr(39), '').replace(chr(34), '')}', "
        family += PREDEFINED_FAMILY
    else:
        family = PREDEFINED_FAMILY
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
