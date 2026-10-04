"""``CaseGlyph`` — case / select / comment: opaque body + a solid bordered box.

The selector widget, per-frame value labels and dropdown menu are inherited from
:class:`SelectableStructureGlyph`. This glyph adds only the static box and the
"A=a" case-insensitive badge. (Disable-family structures are their own glyphs —
see ``disable.py``.)
"""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import DEFAULT_BORDER_W, THICK_FRAME_BORDER_W, Rect, draw_dither_band
from .selectable import SelectableStructureGlyph


class CaseGlyph(SelectableStructureGlyph):
    """A bordered box. ``border_color`` overrides the default border (an
    error-cluster case colours its box by the default frame — green/red, drawn
    slightly bolder, SOLID -- the error colour carries real meaning, so it
    stays a plain border rather than diluted into a dither); otherwise a
    dithered-checker band, per the real reference image (reads as a flat grey
    from a normal viewing distance, without being a solid fill).
    ``case_insensitive`` adds the "A=a" badge of a case-insensitive string
    selector."""

    def __init__(
        self,
        *,
        border_color: str | None = None,
        case_insensitive: bool = False,
    ) -> None:
        self._apply_error_border(border_color)
        self.case_insensitive = case_insensitive

    def interior(self, bounds: Rect) -> Rect:
        if self.border_color is not None:
            return super().interior(bounds)
        x1, y1, x2, y2 = bounds
        w = THICK_FRAME_BORDER_W
        return (x1 + w, y1 + w, x2 - w, y2 - w)

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        if self.border_color is not None:
            super().draw_outline(backend, bounds, theme)  # error-cluster: solid
        else:
            draw_dither_band(
                backend, bounds, THICK_FRAME_BORDER_W, theme.struct_border, theme.canvas
            )
            # A thin crisp outline framing the dither, on top of it -- per
            # the real reference image, the dither alone reads as unbounded.
            x1, y1, x2, y2 = bounds
            backend.rect(
                x1, y1, x2, y2,
                fill="none",
                stroke=theme.struct_border,
                stroke_width=DEFAULT_BORDER_W,
            )
        if self.case_insensitive:
            # Case-insensitivity is a STRING-selector feature, so the badge takes
            # the string-wire colour as a type cue (not a generic text colour).
            x1, _, _, y2 = bounds
            backend.text(
                x1 + 4.0, y2 - 3.5, "A=a", 8.5, fill=theme.wire_string, anchor="start"
            )
