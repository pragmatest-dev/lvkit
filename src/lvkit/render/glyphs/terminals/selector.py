"""``SelectorTerminalGlyph`` — a case/select selector terminal: a box with a
"?" in the wire-type color of whatever feeds it."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class SelectorTerminalGlyph(BorderTerminalGlyph):
    """Takes the WIRE TYPE COLOR of its source (bool→green, enum→blue, error
    cluster→mustard, …); a neutral pale fill keeps the "?" legible. ``color`` is
    the source wire color, or None when nothing feeds it yet."""

    def __init__(self, color: str | None) -> None:
        self.color = color

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        col = self.color or theme.selector_stroke
        backend.rect(
            x1, y1, x2, y2, fill=theme.loop_term_fill, stroke=col, stroke_width=1.2
        )
        backend.text(cx, cy + 4, "?", 10, fill=self.color or theme.selector_text)
