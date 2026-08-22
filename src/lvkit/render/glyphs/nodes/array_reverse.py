from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _ARRAY_ELEMENTS_N, _draw_arrow, _draw_element_boxes, _draw_node_tile


@dataclass(frozen=True)
class ArrayReverseGlyph:
    """Reverse 1D Array: element boxes with a STRAIGHT backward (right-to-
    left) arrow above them — a curved arc would read as "loop", not
    "reverse", matching the approved proof sketch (``g_reverse``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.17, x1 + w * 0.65
        row_y1, row_y2 = y1 + h * 0.47, y1 + h * 0.82
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
        arrow_y = y1 + h * 0.24
        _draw_arrow(
            backend,
            row_x2,
            row_x1,
            arrow_y,
            stroke=stroke,
            stroke_width=sw,
            head_len=max(3.0, w * 0.07),
            head_half_h=max(2.0, h * 0.09),
        )
