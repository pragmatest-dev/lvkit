"""``FlatSequenceGlyph`` — a flat sequence: opaque body + film-strip rails and
per-frame divider lines."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect, StructureBodyGlyph


class FlatSequenceGlyph(StructureBodyGlyph):
    """Outer box + top/bottom rails + a vertical divider at each inter-frame
    boundary (the film-strip look). ``dividers`` are absolute x-positions,
    injected from the layout."""

    def __init__(self, *, dividers: list[float] | None = None) -> None:
        self.dividers = dividers or []

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        s = theme.struct_border
        backend.rect(x1, y1, x2, y2, fill="none", stroke=s, stroke_width=1.2)
        backend.line(x1, y1 + 4, x2, y1 + 4, stroke=s, stroke_width=1)
        backend.line(x1, y2 - 4, x2, y2 - 4, stroke=s, stroke_width=1)
        for dx in self.dividers:
            backend.line(dx, y1, dx, y2, stroke=s, stroke_width=1)
