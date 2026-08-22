"""``ShiftRegisterTerminalGlyph`` — a loop shift register: a type-colored box
with a filled triangle pointing up (left register) or down (right register)."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class ShiftRegisterTerminalGlyph(BorderTerminalGlyph):
    """``up`` selects the triangle direction (the two halves of a shift-register
    pair point opposite ways); ``color`` is the register's wire-type color."""

    def __init__(self, up: bool, color: str | None) -> None:
        self.up = up
        self.color = color

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        cx = (x1 + x2) / 2
        col = self.color or theme.sr_stroke
        backend.rect(
            x1, y1, x2, y2, fill=theme.loop_term_fill, stroke=col, stroke_width=1.2
        )
        if self.up:
            tri = [(x1 + 2, y2 - 2), (cx, y1 + 2), (x2 - 2, y2 - 2)]
        else:
            tri = [(x1 + 2, y1 + 2), (cx, y2 - 2), (x2 - 2, y1 + 2)]
        backend.polygon(tri, fill=col)
