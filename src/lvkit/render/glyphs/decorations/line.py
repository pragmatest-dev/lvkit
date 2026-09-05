"""``LineGlyph`` — LabVIEW's Thin/Thick Line decoration (no arrowhead)."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import THICK_W, THIN_W, DecorationGlyph, Point, Rect, line_endpoints


class LineGlyph(DecorationGlyph):
    """A straight line spanning its bounds. ``thick`` picks the Thick vs Thin
    stroke. ``points`` are the decoration's real per-instance endpoints,
    decoded from its ``ImageInternalsResID`` PICC section; when absent (``()``,
    the default), endpoints fall back to a guess from ``bounds`` alone
    (``line_endpoints``)."""

    def __init__(self, *, thick: bool = False, points: tuple[Point, ...] = ()) -> None:
        self.width = THICK_W if thick else THIN_W
        self.points = points

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        if len(self.points) >= 2:
            (tx, ty), (hx, hy) = self.points[0], self.points[-1]
        else:
            (tx, ty), (hx, hy) = line_endpoints(bounds)
        backend.line(
            tx, ty, hx, hy, stroke=theme.struct_border, stroke_width=self.width
        )
