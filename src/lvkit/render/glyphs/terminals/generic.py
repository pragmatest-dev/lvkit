"""``GenericTerminalGlyph`` — fallback for a border DCO the fixed glyph table
doesn't cover: an undecorated box (never a guessed glyph)."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class GenericTerminalGlyph(BorderTerminalGlyph):
    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, fill="#ffffff", stroke=theme.struct_border, stroke_width=1.0
        )
