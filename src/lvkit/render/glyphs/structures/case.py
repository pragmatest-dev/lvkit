"""``CaseGlyph`` — case / select / comment: opaque body + a solid bordered box.

The selector widget, per-frame value labels and dropdown menu are inherited from
:class:`SelectableStructureGlyph`. This glyph adds only the static box and the
"A=a" case-insensitive badge. (Disable-family structures are their own glyphs —
see ``disable.py``.)
"""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect
from .selectable import SelectableStructureGlyph


class CaseGlyph(SelectableStructureGlyph):
    """A bordered box. ``border_color`` overrides the default border (an
    error-cluster case colours its box by the default frame — green/red, drawn
    slightly bolder); ``case_insensitive`` adds the "A=a" badge of a
    case-insensitive string selector."""

    def __init__(
        self,
        *,
        border_color: str | None = None,
        case_insensitive: bool = False,
    ) -> None:
        self._apply_error_border(border_color)
        self.case_insensitive = case_insensitive

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        super().draw_outline(backend, bounds, theme)  # the solid box
        if self.case_insensitive:
            # Case-insensitivity is a STRING-selector feature, so the badge takes
            # the string-wire colour as a type cue (not a generic text colour).
            x1, _, _, y2 = bounds
            backend.text(
                x1 + 4.0, y2 - 3.5, "A=a", 8.5, fill=theme.wire_string, anchor="start"
            )
