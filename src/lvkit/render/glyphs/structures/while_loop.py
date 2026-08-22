"""``WhileLoopGlyph`` — the While-Loop's opaque rounded body + loop-back arrow."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect, StructureBodyGlyph


class WhileLoopGlyph(StructureBodyGlyph):
    """A rounded-rectangle loop with an OPAQUE body and the bottom-right
    loop-back arrowhead that marks it as a While loop (a For loop uses the
    stacked cards instead)."""

    def draw_body(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(x1, y1, x2, y2, rx=7, fill=theme.canvas, stroke=None)

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, rx=7, fill="none", stroke=theme.struct_border,
            stroke_width=1.2,
        )
        # Small right-pointing triangle on the bottom edge, just inside the
        # corner — the "this is a loop" cue.
        s = 7.0
        backend.polygon(
            [(x2 - s, y2 - s * 0.55), (x2 + s * 0.15, y2), (x2 - s, y2 + s * 0.55)],
            fill=theme.struct_border,
            stroke=None,
        )
