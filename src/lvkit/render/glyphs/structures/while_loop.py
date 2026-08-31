"""``WhileLoopGlyph`` — the While-Loop's opaque rounded body + loop-back arrow."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect, StructureBodyGlyph

# As wide as the For loop's whole 3-card stack (2*_O offset + line) ≈ 5.2px, and
# a matching corner radius — both read from the reference.
WHILE_BORDER_W = 5.2
WHILE_RADIUS = 5.75


class WhileLoopGlyph(StructureBodyGlyph):
    """A rounded-rectangle loop with an OPAQUE body and a THICK GREY border
    (vs the For loop's thin near-black stacked cards), plus the bottom-right
    loop-back arrowhead that marks it as a While loop."""

    border_width = WHILE_BORDER_W
    radius = WHILE_RADIUS

    def draw_body(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(x1, y1, x2, y2, rx=self.radius, fill=theme.canvas, stroke=None)

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        c = theme.while_border
        w = self.border_width
        backend.rect(
            x1, y1, x2, y2, rx=self.radius, fill="none", stroke=c, stroke_width=w
        )
        # The While-loop cue at the bottom-right: a 45-45-90 arrowhead with the
        # RIGHT ANGLE at the top, a 45 at the bottom-right corner and a 45 to its
        # left (hypotenuse bottom-right → up-left), sitting flush at the outer
        # corner (the backend insets the box rect, so the border's outer edge is
        # the bounds), plus a short GAP in the right border just above it, so it
        # reads as a loop-back. The arrow is unstroked, so it is NOT auto-inset.
        a = w * 1.75  # arrow leg, scaled with the border
        ax, ay = x2, y2
        backend.polygon([(ax, ay - a), (ax, ay), (ax - a, ay - a)], fill=c, stroke=None)
        gap = w * 0.5  # erase a short border segment above the arrow, back to canvas
        backend.rect(
            x2 - w, ay - a - gap, x2 + w, ay - a, fill=theme.canvas, stroke=None
        )
