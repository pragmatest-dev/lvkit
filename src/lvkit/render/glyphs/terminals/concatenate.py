"""``ConcatenateTerminalGlyph`` — an auto-concatenating output tunnel: a pale
box with TWO filled blocks side by side (vs a plain tunnel's single block)."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class ConcatenateTerminalGlyph(BorderTerminalGlyph):
    """The loop concatenates each iteration's array into one output array of the
    same dimension; LabVIEW marks that with two blocks in the wire-type color.
    ``color`` is the output wire-type color."""

    def __init__(self, color: str | None) -> None:
        self.color = color

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        col = self.color or theme.wire_default
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=theme.loop_term_fill,
            stroke=theme.tunnel_border,
            stroke_width=1.2,
        )
        sq = min(x2 - x1, y2 - y1) * 0.30
        gap = sq * 0.40
        left = cx - (2 * sq + gap) / 2
        for sx in (left, left + sq + gap):
            backend.rect(sx, cy - sq / 2, sx + sq, cy + sq / 2, fill=col, stroke="none")
