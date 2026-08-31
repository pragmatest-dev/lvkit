"""``LoopCountTerminalGlyph`` — a loop's count (N) or iteration (i) terminal:
a pale box holding an italic letter."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class LoopCountTerminalGlyph(BorderTerminalGlyph):
    """The N (count) and i (iteration) terminals share one glyph — a box with
    the italic letter; ``letter`` selects which."""

    def __init__(self, letter: str) -> None:
        self.letter = letter

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=theme.loop_term_fill,
            stroke=theme.loop_term,
            stroke_width=1.5,
        )
        backend.text(
            cx, cy + 4, self.letter, 11, fill=theme.loop_term_text, italic=True
        )
