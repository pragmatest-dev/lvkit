"""A free decoration on a front panel: its shape from its stored ``ImageResID``.

The table below holds only the ids the corpus evidence settles (the shape
signature of every id over 618 free decorations: its box proportions, its fg /
bg colours, whether controls are drawn in front of it); an id outside it draws
its box outline. NI's public documentation names no shape by id, so the table
describes the shape each id draws, not its palette name. The ids the block
diagram already draws (a frame, thin and thick lines, arrows) are drawn by the
block-diagram glyphs.
"""

from __future__ import annotations

from enum import Enum

from ....parser.layout import Rect
from ....parser.models import ParsedFPDecoration
from ...backend import Backend
from ...glyphs.decorations.base import line_endpoints
from ...glyphs.decorations.factory import KNOWN_IDS, decoration_glyph
from ...style import Theme


class DecorationShape(Enum):
    FILLED_BOX = "filled box"
    VERTICAL_LINE = "line"
    ARROW_HEAD = "arrow head"
    PICTURE = "embedded picture"
    OUTLINE = "box outline"


_SHAPES: dict[int, DecorationShape] = {
    # A big box (57..1008 tall, 106..1200 wide) with controls drawn in front of
    # it and, for -412 / -18, usually the last in its list: the panel's backdrop.
    -412: DecorationShape.FILLED_BOX,  # 78 uses, bg the window colour
    -18: DecorationShape.FILLED_BOX,  # 15 uses, 12 last in the list, bg B3B3B3
    -11: DecorationShape.FILLED_BOX,  # 19 uses, 9 last in the list, bg B3B3B3
    # Thin bars: 3..5 px wide and 37..601 px tall, nothing in front of them.
    -504: DecorationShape.VERTICAL_LINE,  # 23 uses, 5 wide
    -503: DecorationShape.VERTICAL_LINE,  # 8 uses, 3 wide
    -428: DecorationShape.VERTICAL_LINE,  # 8 uses, 5 wide
    # Exactly 6x4 and 4x6, both colours 333333, in pairs.
    -117: DecorationShape.ARROW_HEAD,  # 29 uses, 6 wide, 4 tall
    -115: DecorationShape.ARROW_HEAD,  # 29 uses, 4 wide, 6 tall
}


def decoration_shape(image_res_id: int) -> DecorationShape | None:
    """The shape the evidence settles for ``image_res_id``, None for an id that
    draws its box outline. A positive id is an embedded picture."""
    if image_res_id in _SHAPES:
        return _SHAPES[image_res_id]
    if image_res_id > 0:
        return DecorationShape.PICTURE
    return None


def draw_decoration(
    deco: ParsedFPDecoration, bounds: Rect, backend: Backend, theme: Theme
) -> None:
    """Draw ``deco`` into its placed ``bounds``. A literal colour the file
    records is drawn as recorded; otherwise the theme's."""
    if str(deco.image_res_id) in KNOWN_IDS:
        decoration_glyph(str(deco.image_res_id)).draw(backend, bounds, theme)
        return
    stroke = deco.fg_color or theme.struct_border
    shape = decoration_shape(deco.image_res_id)
    x1, y1, x2, y2 = bounds
    if shape is DecorationShape.FILLED_BOX:
        backend.rect(
            x1, y1, x2, y2, fill=deco.bg_color or theme.fp_panel, stroke=deco.fg_color
        )
    elif shape is DecorationShape.VERTICAL_LINE:
        (tx, ty), (hx, hy) = line_endpoints(bounds)
        backend.line(tx, ty, hx, hy, stroke=stroke, stroke_width=1.0)
    elif shape is DecorationShape.ARROW_HEAD:
        backend.polygon(_arrow_head(bounds), fill=stroke, stroke=None)
    else:
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=deco.bg_color if shape is DecorationShape.PICTURE else "none",
            stroke=stroke,
            stroke_width=1.0,
        )


def _arrow_head(bounds: Rect) -> list[tuple[float, float]]:
    """A triangle filling ``bounds``, its tip at the left of a wide box and at
    the top of a tall one (the direction the block-diagram arrows use)."""
    x1, y1, x2, y2 = bounds
    if x2 - x1 >= y2 - y1:
        return [(x1, (y1 + y2) / 2), (x2, y1), (x2, y2)]
    return [((x1 + x2) / 2, y1), (x1, y2), (x2, y2)]
