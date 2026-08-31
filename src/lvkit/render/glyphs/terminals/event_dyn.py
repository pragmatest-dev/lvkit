"""``EventDynTerminalGlyph`` — an Event Structure's dynamic-event-registration
terminal: a dark-green (refnum-colored) box with a right-pointing arrow."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class EventDynTerminalGlyph(BorderTerminalGlyph):
    """Both terminals of the ``otherSide``-linked pair draw their arrow pointing
    RIGHT — the registration refnum threads left-to-right through the structure,
    so the glyph follows flow direction on both edges rather than mirroring."""

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        col = theme.wire_refnum
        backend.rect(
            x1, y1, x2, y2, fill=theme.loop_term_fill, stroke=col, stroke_width=1.2
        )
        aw = (x2 - x1) * 0.22
        ah = (y2 - y1) * 0.26
        backend.polygon(
            [(cx - aw, cy - ah), (cx - aw, cy + ah), (cx + aw, cy)],
            fill=col,
            stroke=None,
        )
