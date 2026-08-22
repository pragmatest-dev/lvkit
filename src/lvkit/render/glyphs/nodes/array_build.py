from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _draw_node_tile


@dataclass(frozen=True)
class ArrayBuildGlyph:
    """Build Array (``aBuild``): a DRAWER that grows a row taller per wired
    input — the input terminals themselves make the rows, so the node's bounds
    (and this tile) grow for free. The body carries a clean-room ARRAY BRACKET
    enclosing a column of element cells (the appended array), modelled on the
    real LabVIEW Build Array icon — so it reads as array-assembly and is NOT
    mistaken for a Bundle (which uses the generic ``draw_split_box`` skeleton).
    ``num_inputs`` is kept for reference; growth comes from the bounds."""

    num_inputs: int = 1
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.045)
        # Array bracket [ ] enclosing a stacked column of element cells.
        bx1, bx2 = x1 + w * 0.28, x2 - w * 0.12
        by1, by2 = y1 + h * 0.13, y2 - h * 0.13
        foot = (bx2 - bx1) * 0.28
        backend.path(
            [(bx1 + foot, by1), (bx1, by1), (bx1, by2), (bx1 + foot, by2)],
            stroke=stroke,
            stroke_width=sw,
        )
        backend.path(
            [(bx2 - foot, by1), (bx2, by1), (bx2, by2), (bx2 - foot, by2)],
            stroke=stroke,
            stroke_width=sw,
        )
        cx1, cx2 = bx1 + foot * 1.15, bx2 - foot * 1.15
        n_cells = max(2, min(5, int((by2 - by1) / max(1.0, w * 0.24))))
        gap = (by2 - by1) / n_cells
        cell_h = gap * 0.6
        for i in range(n_cells):
            cyy = by1 + i * gap + (gap - cell_h) / 2
            backend.rect(
                cx1,
                cyy,
                cx2,
                cyy + cell_h,
                fill="none",
                stroke=stroke,
                stroke_width=sw * 0.8,
            )
