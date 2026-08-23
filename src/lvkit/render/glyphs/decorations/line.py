"""``LineGlyph`` — LabVIEW's Thin/Thick Line decoration (no arrowhead)."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import THICK_W, THIN_W, DecorationGlyph, Rect, line_endpoints


class LineGlyph(DecorationGlyph):
    """A straight line spanning its bounds (orientation from the bounds; see
    ``line_endpoints``). ``thick`` picks the Thick vs Thin stroke."""

    def __init__(self, *, thick: bool = False) -> None:
        self.width = THICK_W if thick else THIN_W

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        (tx, ty), (hx, hy) = line_endpoints(bounds)
        backend.line(
            tx, ty, hx, hy, stroke=theme.struct_border, stroke_width=self.width
        )
