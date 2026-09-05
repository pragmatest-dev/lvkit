"""``ArrowGlyph`` — LabVIEW's Thin/Thick Line WITH Arrow decoration."""

from __future__ import annotations

import math

from ...backend import Backend
from ...style import Theme
from .base import THICK_W, THIN_W, DecorationGlyph, Point, Rect, line_endpoints


class ArrowGlyph(DecorationGlyph):
    """A line spanning its bounds with a solid triangular arrowhead at the
    ``head`` end. ``thick`` picks the Thick vs Thin stroke; the head scales
    with it. The head's LENGTH (along the shaft) and base HALF-WIDTH are
    independent so it's a long narrow arrowhead, not an equilateral one; the
    shaft stops at the head's base so the thick line never pokes through the
    point.

    ``points`` are the decoration's real per-instance endpoints, decoded from
    its ``ImageInternalsResID`` PICC section (``points[0]`` tail, ``points[-1]``
    head — see labview-binary-format.md); when absent (``()``, the default),
    endpoints fall back to a guess from ``bounds`` alone (``line_endpoints``)."""

    def __init__(self, *, thick: bool = False, points: tuple[Point, ...] = ()) -> None:
        self.width = THICK_W if thick else THIN_W
        self.points = points

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        if len(self.points) >= 2:
            (tx, ty), (hx, hy) = self.points[0], self.points[-1]
        else:
            (tx, ty), (hx, hy) = line_endpoints(bounds)
        color = theme.struct_border
        ang = math.atan2(hy - ty, hx - tx)
        head_len = 7.0 + 2.0 * self.width  # axial length (tip -> base)
        head_half = 2.0 + 1.0 * self.width  # base half-width (perpendicular)
        # Base midpoint on the shaft axis; the shaft stops here.
        bx, by = hx - head_len * math.cos(ang), hy - head_len * math.sin(ang)
        px, py = math.cos(ang + math.pi / 2), math.sin(ang + math.pi / 2)
        c1 = (bx + head_half * px, by + head_half * py)
        c2 = (bx - head_half * px, by - head_half * py)
        backend.line(tx, ty, bx, by, stroke=color, stroke_width=self.width)
        backend.polygon([(hx, hy), c1, c2], fill=color, stroke=None)
