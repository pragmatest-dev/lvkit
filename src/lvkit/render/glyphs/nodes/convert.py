from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _draw_arrow, _draw_node_tile


@dataclass(frozen=True)
class ConvertGlyph:
    """A numeric/type CONVERTER primitive: a small entering arrow feeding a
    bold target-type abbreviation ("going to that type") — e.g. "To Long
    Integer" -> "I32". Matches the approved proof sketch (``conv``):
    monochrome outline arrow, bold monospace abbreviation, scaled to the
    node's real bounds (no fixed pixel size)."""

    abbr: str
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        cy = (y1 + y2) / 2
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.07)
        arrow_x1, arrow_x2 = x1 + w * 0.06, x1 + w * 0.30
        _draw_arrow(
            backend,
            arrow_x1,
            arrow_x2,
            cy,
            stroke=stroke,
            stroke_width=sw,
            head_len=max(3.0, w * 0.08),
            head_half_h=max(2.0, h * 0.14),
        )
        text_x = x1 + w * 0.34
        avail_w = max(2.0, (x2 - w * 0.04) - text_x)
        size = max(6.0, min(15.0, h * 0.55))
        while size > 6.0 and backend.measure_text(self.abbr, size) > avail_w:
            size -= 0.5
        backend.text(
            text_x,
            cy + size * 0.34,
            self.abbr,
            size,
            fill=text_fill,
            bold=True,
            mono=True,
            anchor="start",
        )
