"""``EventTimeoutTerminalGlyph`` — an Event Structure's timeout input: a box
holding a blue hourglass (two triangles apex-to-apex)."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class EventTimeoutTerminalGlyph(BorderTerminalGlyph):
    """Sits where a For-Loop's N terminal would; a real wireable INPUT (the
    timeout value), so it gets the pale loop-term fill with a bespoke hourglass
    in the wire-integer color."""

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        backend.rect(
            x1, y1, x2, y2, fill=theme.loop_term_fill, stroke=theme.wire_int,
            stroke_width=1.2,
        )
        hw = (x2 - x1) * 0.30
        hh = (y2 - y1) * 0.34
        backend.polygon(
            [(cx - hw, cy - hh), (cx + hw, cy - hh), (cx, cy)],
            fill=theme.wire_int, stroke=None,
        )
        backend.polygon(
            [(cx - hw, cy + hh), (cx + hw, cy + hh), (cx, cy)],
            fill=theme.wire_int, stroke=None,
        )
