"""``AutoIndexTerminalGlyph`` — an array auto-indexing tunnel: a pale box with
element brackets ``[ ]`` drawn as long-serif shapes in the wire-type color."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class AutoIndexTerminalGlyph(BorderTerminalGlyph):
    """The brackets sit padded inside the box with LONG serifs so ``[`` and ``]``
    nearly meet top and bottom — reading as a square inside the square.
    ``color`` is the element wire-type color."""

    def __init__(self, color: str | None) -> None:
        self.color = color

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        col = self.color or "#333333"
        backend.rect(
            x1, y1, x2, y2, fill=theme.loop_term_fill, stroke=theme.tunnel_border,
            stroke_width=1.2,
        )
        side = min(x2 - x1, y2 - y1) * (1 - 2 * 0.26)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        lx, rx = mx - side / 2, mx + side / 2
        ty, by2 = my - side / 2, my + side / 2
        sr = side * 0.40  # long serifs: [ and ] nearly close at top/bottom
        backend.path(
            [(lx + sr, ty), (lx, ty), (lx, by2), (lx + sr, by2)],
            stroke=col, stroke_width=1.3,
        )
        backend.path(
            [(rx - sr, ty), (rx, ty), (rx, by2), (rx - sr, by2)],
            stroke=col, stroke_width=1.3,
        )
