"""``FlatSequenceGlyph`` — a flat sequence: opaque body + film-strip rails and
per-frame divider lines."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import RAIL_W, Rect, StructureBodyGlyph


class FlatSequenceGlyph(StructureBodyGlyph):
    """Outer box + top/bottom rails + a vertical divider at each inter-frame
    boundary (the film-strip look). ``dividers`` are absolute x-positions,
    injected from the layout; ``border_color`` colours an error-cluster boundary
    (same treatment as case/stacked)."""

    def __init__(
        self, *, dividers: list[float] | None = None, border_color: str | None = None
    ) -> None:
        self.dividers = dividers or []
        self._apply_error_border(border_color)

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        _, y1, _, y2 = bounds
        s = self.border_color or theme.struct_border
        super().draw_outline(backend, bounds, theme)  # the box (colour + width)
        self._draw_rails(backend, bounds, s)
        for dx in self.dividers:
            backend.line(dx, y1, dx, y2, stroke=s, stroke_width=RAIL_W)
