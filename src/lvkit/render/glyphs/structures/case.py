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
    error-cluster case colours its box by the default frame — green/red, drawn
    slightly bolder); ``dotted`` draws LabVIEW's dashed disable-structure
    boundary; ``case_insensitive`` adds the "A=a" badge of a case-insensitive
    string selector."""

    def __init__(
        self,
        *,
        border_color: str | None = None,
        dotted: bool = False,
        case_insensitive: bool = False,
    ) -> None:
        self._apply_error_border(border_color)
        self.dotted = dotted
        self.case_insensitive = case_insensitive

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, fill="none",
            stroke=self.border_color or theme.struct_border,
            stroke_width=self.border_width,
            stroke_dasharray="1.5,2.5" if self.dotted else None,
        )
        if self.case_insensitive:
            # Case-insensitivity is a STRING-selector feature, so the badge takes
            # the string-wire colour as a type cue (not a generic text colour).
            backend.text(
                x1 + 4.0, y2 - 3.5, "A=a", 8.5, fill=theme.wire_string, anchor="start"
            )
