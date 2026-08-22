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
        self._apply_error_border(border_color)

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        super().draw_outline(backend, bounds, theme)  # the box (colour + width)
        # Rails follow the box colour so an error-coloured sequence stays uniform.
        self._draw_rails(backend, bounds, self.border_color or theme.struct_border)
