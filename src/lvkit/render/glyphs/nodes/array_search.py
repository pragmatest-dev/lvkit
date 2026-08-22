from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _ARRAY_ELEMENTS_N, _draw_element_boxes, _draw_node_tile


@dataclass(frozen=True)
class ArraySearchGlyph:
    """Search 1D Array: element boxes with a magnifier (circle + short
    handle) over one of them, matching the approved proof sketch
    (``g_search``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.04, x1 + w * 0.60
        row_y1, row_y2 = y1 + h * 0.38, y1 + h * 0.74
        _draw_element_boxes(
            backend,
            row_x1,
            row_y1,
            row_x2,
            row_y2,
            _ARRAY_ELEMENTS_N,
            stroke,
            sw,
        )
        r = max(2.0, min(w, h) * 0.18)
        cx, cy = x1 + w * 0.74, y1 + h * 0.30
        backend.circle(cx, cy, r, fill="none", stroke=stroke, stroke_width=sw)
        hx, hy = r * 0.78, r * 0.78
        backend.path(
            [(cx + hx * 0.65, cy + hy * 0.65), (cx + hx * 1.55, cy + hy * 1.55)],
            stroke=stroke,
            stroke_width=sw,
        )
