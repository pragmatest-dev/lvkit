"""``ForLoopGlyph`` — the For-Loop's opaque body + stacked-card outline."""

from __future__ import annotations

from ...backend import Backend, _stroke_inset
from ...style import Theme
from .base import Rect, StructureBodyGlyph

_O = 2.0  # card fan offset — body silhouette and card outline must agree
_DOG_EAR = 6.0  # front-card bottom-right fold size


class ForLoopGlyph(StructureBodyGlyph):
    """The For-Loop's signature stacked-card border, now with an OPAQUE body.

    The body fills the stacked-card SILHOUETTE (canvas colour) so anything
    behind the loop is occluded — but the top-right and bottom-left NOTCHES
    outside the stairstep stay transparent, so a sibling behind a notch shows
    through (LabVIEW does the same). The outline then strokes three identical
    cards fanning down-right from a top-left-aligned front card (only each back
    card's visible L is stroked). Ground-truth geometry: heap bounds are the
    BACKMOST card's bottom-right; the front card is (2o x 2o) smaller and
    top-left-aligned.
    """

    def draw_body(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        o = _O
        fx2, fy2 = x2 - 2 * o, y2 - 2 * o  # front card bottom-right
        # Outer silhouette of the three fanned cards, clockwise from top-left:
        # a rising staircase down the right edge, a matching one up the bottom-
        # left. The notch above/right of the steps and below/left is left open.
        backend.polygon(
            [
                (x1, y1),
                (fx2, y1),
                (fx2, y1 + o),
                (fx2 + o, y1 + o),
                (fx2 + o, y1 + 2 * o),
                (x2, y1 + 2 * o),
                (x2, y2),
                (x1 + 2 * o, y2),
                (x1 + 2 * o, fy2 + o),
                (x1 + o, fy2 + o),
                (x1 + o, fy2),
                (x1, fy2),
            ],
            fill=theme.canvas,
        )

    def interior(self, bounds: Rect) -> Rect:
        # Contents live inside the front card (not the outer stacked bounds),
        # border-inset on every side like every other kind.
        x1, y1, x2, y2 = bounds
        return self._clip_inset((x1, y1, x2 - 2 * _O, y2 - 2 * _O))

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        s = theme.struct_border
        w = self.border_width
        # The cards are freeform paths, which the backend does NOT auto-inset,
        # so pull the working rect in by the same half-stroke every bounded
        # outline uses — the card outer edges then land on the OUTER bounds.
        i = _stroke_inset(s, w)
        x1, y1, x2, y2 = bounds
        x1, y1, x2, y2 = x1 + i, y1 + i, x2 - i, y2 - i
        o = _O
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
                stroke_width=w,
            )
        # Front card (loop boundary), top-left-aligned, dog-eared bottom-right.
        f = _DOG_EAR
        backend.path(
            [(x1, y1), (fx2, y1), (fx2, fy2 - f), (fx2 - f, fy2), (x1, fy2), (x1, y1)],
            fill="none",
            stroke=s,
            stroke_width=w,
        )
        backend.path(
            [(fx2, fy2 - f), (fx2 - f, fy2 - f), (fx2 - f, fy2)],
            stroke=s,
            stroke_width=w,
        )
