from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _ARRAY_ELEMENTS_N, _draw_element_boxes, _draw_node_tile


@dataclass(frozen=True)
class ArraySortGlyph:
    """Sort 1D Array: element boxes with ascending bars beside them — NO
    arrow (sorting implies resulting order, not travel direction), matching
    the approved proof sketch (``g_sort``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.06, x1 + w * 0.62
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
        n_bars = 3
        bars_x1, bars_x2 = x1 + w * 0.68, x2 - w * 0.04
        span = bars_x2 - bars_x1
        bar_w = span / n_bars * 0.6
        step = span / n_bars
        base_y = y1 + h * 0.80
        max_bar_h = h * 0.56
        for i in range(n_bars):
            bh = max_bar_h * (i + 1) / n_bars
            bx = bars_x1 + i * step
            backend.rect(bx, base_y - bh, bx + bar_w, base_y, fill=stroke)
