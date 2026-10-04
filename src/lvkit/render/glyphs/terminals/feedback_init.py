"""``FeedbackInitTerminalGlyph`` — a Feedback Node's initializer terminal
"moved one loop out" (LabVIEW's own menu item) onto the owning loop's
border, instead of staying part of the Feedback Node's own icon.

Verified directly against the issue's own reference image (a real LabVIEW
screenshot): a light (cream/canvas) box, type-colored OUTLINE, with a small
FILLED DIAMOND in that same type color centered inside -- dark-on-light,
the same convention as the Feedback Node's own unwired-initializer asterisk
marker (``glyphs.nodes.feedback_node``), NOT a solid type-colored fill with
a light dot (that reading, from a paraphrased forum description alone, was
wrong -- this glyph exists specifically because the reference image was
checked, not inferred from text). See ``parser.nodes.loop``'s
``initFeedback`` handling for how the border dco (linked to its Feedback
Node via ``rsrDCO``) is identified.
"""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import BorderTerminalGlyph, Rect


class FeedbackInitTerminalGlyph(BorderTerminalGlyph):
    """A light box with a type-colored outline and a small filled diamond
    in that same color, centered -- the diamond is the one visual
    difference from an ordinary last-value tunnel block, marking this as a
    Feedback Node's initializer rather than a plain passthrough."""

    def __init__(self, color: str | None) -> None:
        self.color = color

    def _draw(
        self, backend: Backend, bounds: Rect, theme: Theme, frame_value: str | None
    ) -> None:
        x1, y1, x2, y2 = bounds
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        col = self.color or theme.wire_default
        backend.rect(
            x1, y1, x2, y2, fill=theme.loop_term_fill, stroke=col, stroke_width=1.2
        )
        r = min(x2 - x1, y2 - y1) * 0.28
        backend.polygon(
            [(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=col
        )
