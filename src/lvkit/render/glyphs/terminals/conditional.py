"""``ConditionalTerminalGlyph`` — a while-loop's conditional (stop) terminal:
a boxed disc, red for Stop-if-True or green for Continue-if-True."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class ConditionalTerminalGlyph(BorderTerminalGlyph):
    """LabVIEW shows the loop's mode by color: RED Stop-if-True (default) vs
    GREEN Continue-if-True (``cond_continue``, from the loop's
    ``stop_condition_inverted``). Continue also gets a small white loop arc so
    the two read apart without relying on color."""

    def __init__(self, cond_continue: bool) -> None:
        self.cond_continue = cond_continue

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        mode_col = theme.cond_continue if self.cond_continue else theme.cond_stop
        backend.rect(
            x1, y1, x2, y2, fill=theme.loop_term_fill, stroke=mode_col, stroke_width=1.2
        )
        r = min(x2 - x1, y2 - y1) / 2 * 0.62
        if self.cond_continue:
            backend.circle(cx, cy, r, fill=theme.cond_continue)
            a = r * 0.55
            backend.path(
                [
                    (cx - a, cy + a * 0.2),
                    (cx - a, cy - a),
                    (cx + a, cy - a),
                    (cx + a, cy + a),
                ],
                stroke="#ffffff",
                stroke_width=1.2,
            )
            backend.polygon(
                [
                    (cx + a - 1.6, cy + a - 1.8),
                    (cx + a + 1.6, cy + a - 1.8),
                    (cx + a, cy + a + 1.4),
                ],
                fill="#ffffff",
                stroke=None,
            )
        else:
            backend.circle(cx, cy, r, fill=theme.cond_stop)
