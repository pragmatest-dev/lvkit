from __future__ import annotations

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


def draw_terminal_frame(
    backend: Backend, bounds: Rect, color: str, theme: Theme
) -> float:
    """A terminal's frame: a grey panel in a double border of the type colour.
    Returns the inset of the inner border."""
    x1, y1, x2, y2 = bounds
    backend.rect(x1, y1, x2, y2, fill=theme.fp_panel, stroke=color, stroke_width=1.0)
    inset = min(1.6, (x2 - x1) / 4, (y2 - y1) / 4)
    if inset > 0.3:
        backend.rect(
            x1 + inset,
            y1 + inset,
            x2 - inset,
            y2 - inset,
            fill="none",
            stroke=color,
            stroke_width=1.0,
        )
    return inset
