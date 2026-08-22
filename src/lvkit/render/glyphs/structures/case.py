"""``CaseGlyph`` — case / select / diagram-disable: opaque body + bordered box.

The selector widget, per-frame value labels and dropdown menu are interactive
chrome drawn by the composite tree, NOT here. This glyph draws only the static
box (colour/dash injected) and the "A=a" case-insensitive badge.
"""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect, StructureBodyGlyph


class CaseGlyph(StructureBodyGlyph):
    """A bordered box. ``border_color`` overrides the default border (an
    error-cluster case colours its box by the default frame — green/red);
    ``dotted`` draws LabVIEW's dashed disable-structure boundary;
    ``case_insensitive`` adds the "A=a" badge of a case-insensitive string
    selector."""

    def __init__(
        self,
        *,
        border_color: str | None = None,
        dotted: bool = False,
        case_insensitive: bool = False,
    ) -> None:
        self.border_color = border_color
        self.dotted = dotted
        self.case_insensitive = case_insensitive

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        dash = "1.5,2.5" if self.dotted else None
        stroke = self.border_color or theme.struct_border
        width = 1.6 if self.border_color is not None else 1.2
        backend.rect(
            x1, y1, x2, y2, fill="none", stroke=stroke, stroke_width=width,
            stroke_dasharray=dash,
        )
        if self.case_insensitive:
            backend.text(
                x1 + 4.0, y2 - 3.5, "A=a", 8.5, fill=theme.wire_string, anchor="start"
            )
