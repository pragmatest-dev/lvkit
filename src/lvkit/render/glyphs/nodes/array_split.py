from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _draw_arrow, _draw_element_boxes, _draw_node_tile


@dataclass(frozen=True)
class ArraySplitGlyph:
    """Split 1D Array: one array splitting into two, matching the approved
    proof sketch (``g_split``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.04, x1 + w * 0.36
        row_y1, row_y2 = y1 + h * 0.38, y1 + h * 0.74
        _draw_element_boxes(backend, row_x1, row_y1, row_x2, row_y2, 2, stroke, sw)
        arrow_y = (row_y1 + row_y2) / 2
        arrow_x2 = x1 + w * 0.55
        _draw_arrow(
            backend,
            row_x2,
            arrow_x2,
            arrow_y,
            stroke=stroke,
            stroke_width=sw,
            head_len=max(3.0, w * 0.06),
            head_half_h=max(2.0, h * 0.08),
        )
        side = min(w, h) * 0.22
        box_x1 = x1 + w * 0.60
        backend.rect(
            box_x1,
            y1 + h * 0.10,
            box_x1 + side,
            y1 + h * 0.10 + side,
            fill="none",
            stroke=stroke,
            stroke_width=sw,
        )
        backend.rect(
            box_x1,
            y1 + h * 0.58,
            box_x1 + side,
            y1 + h * 0.58 + side,
            fill="none",
            stroke=stroke,
            stroke_width=sw,
        )
