from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _ARRAY_ELEMENTS_N, _draw_element_boxes, _draw_node_tile


@dataclass(frozen=True)
class ArraySizeGlyph:
    """Array Size: a row of element boxes over a dimension bracket labeled
    "n" — the array's length shown as a bracketed span beneath its
    elements, matching the approved proof sketch (``g_size``)."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.06)
        row_x1, row_x2 = x1 + w * 0.04, x1 + w * 0.66
        row_y1, row_y2 = y1 + h * 0.10, y1 + h * 0.52
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
        by1, by2 = y1 + h * 0.66, y1 + h * 0.80
        backend.path(
            [(row_x1, by1), (row_x1, by2), (row_x2, by2), (row_x2, by1)],
            stroke=stroke,
            stroke_width=sw,
        )
        size = max(6.0, min(12.0, h * 0.42))
        backend.text(
            x1 + w * 0.85,
            by2 + size * 0.28,
            "n",
            size,
            fill=text_fill,
            bold=True,
        )
