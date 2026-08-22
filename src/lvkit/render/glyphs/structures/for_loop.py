"""``ForLoopGlyph`` — the For-Loop's opaque body + stacked-card outline."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect, StructureBodyGlyph


class ForLoopGlyph(StructureBodyGlyph):
    """The For-Loop's signature stacked-card border, now with an OPAQUE body.

    The body fills the whole footprint (canvas colour) so anything behind the
    loop is occluded. The outline then strokes three identical cards fanning
    down-right from a top-left-aligned front card (only each back card's visible
    L is stroked). Ground-truth geometry: heap bounds are the BACKMOST card's
    bottom-right; the front card is (2o x 2o) smaller and top-left-aligned.
    """

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        o = 2.0
        s = theme.struct_border
        w2, h2 = (x2 - x1) - 2 * o, (y2 - y1) - 2 * o
        fx2, fy2 = x1 + w2, y1 + h2  # front card bottom-right
        for k in (2, 1):  # back + mid cards, offset +k*o down-right of the front
            backend.path(
                [
                    (fx2, y1 + k * o),
                    (fx2 + k * o, y1 + k * o),
                    (fx2 + k * o, fy2 + k * o),
                    (x1 + k * o, fy2 + k * o),
                    (x1 + k * o, fy2),
                ],
                fill="none",
                stroke=s,
                stroke_width=1.2,
            )
        # Front card (loop boundary), top-left-aligned, dog-eared bottom-right.
        f = 6.0
        backend.path(
            [(x1, y1), (fx2, y1), (fx2, fy2 - f), (fx2 - f, fy2), (x1, fy2), (x1, y1)],
            fill="none",
            stroke=s,
            stroke_width=1.2,
        )
        backend.path(
            [(fx2, fy2 - f), (fx2 - f, fy2 - f), (fx2 - f, fy2)],
            stroke=s,
            stroke_width=1.2,
        )
