"""``StackedSequenceGlyph`` — a stacked sequence: opaque body + boxed film-strip
rails (one frame shown at a time; the ``◄ index ►`` selector is tree chrome)."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect, StructureBodyGlyph


class StackedSequenceGlyph(StructureBodyGlyph):
    """A bordered box sharing the flat sequence's top/bottom rails — the
    selector plus single-frame layout is all that distinguishes stacked from
    flat."""

    def __init__(self, *, border_color: str | None = None) -> None:
        self.border_color = border_color

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = self.border_color or theme.struct_border
        width = 1.6 if self.border_color is not None else 1.2
        backend.rect(x1, y1, x2, y2, fill="none", stroke=stroke, stroke_width=width)
        backend.line(x1, y1 + 4, x2, y1 + 4, stroke=theme.struct_border, stroke_width=1)
        backend.line(x1, y2 - 4, x2, y2 - 4, stroke=theme.struct_border, stroke_width=1)
