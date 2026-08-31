"""``FallbackGlyph`` — an unmapped decoration ``ImageResID``.

Rather than guess a shape from geometry (a heuristic), an unknown decoration is
drawn as a neutral dashed outline of its own bounds — honest and never a crash.
The unknown id is logged once so the ``ImageResID`` -> shape map can be extended
from real evidence (see ``factory.decoration_glyph``).
"""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import THIN_W, DecorationGlyph, Rect


class FallbackGlyph(DecorationGlyph):
    """A dashed placeholder box for an unrecognised decoration id."""

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill="none",
            stroke=theme.struct_border,
            stroke_width=THIN_W,
            stroke_dasharray="3,3",
        )
