"""``TunnelConditionTerminalGlyph`` — the condition terminal of a Conditional
loop output tunnel: the Boolean input, just inside the loop border under its
tunnel, that decides on each iteration whether the tunnel takes the value.

A light box with a type-colored outline and a ``?`` in that same color --
dark-on-light like ``FeedbackInitTerminalGlyph``, the ``?`` marking it as the
tunnel's condition rather than another tunnel.
"""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class TunnelConditionTerminalGlyph(BorderTerminalGlyph):
    def __init__(self, color: str | None) -> None:
        self.color = color

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        col = self.color or theme.wire_default
        backend.rect(
            x1, y1, x2, y2, fill=theme.loop_term_fill, stroke=col, stroke_width=1.2
        )
        size = (y2 - y1) * 0.85
        backend.text(
            (x1 + x2) / 2, (y1 + y2) / 2 + size * 0.36, "?", size, fill=col, bold=True
        )
