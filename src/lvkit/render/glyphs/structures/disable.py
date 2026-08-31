"""The disable-family structure glyphs (all serialize as ``commentNode``, but
render per subtype — the composite routes by ``DisableStructureKind``):

* :class:`DisableGlyph` — Diagram / Conditional Disable: a DOTTED bordered box.
  The two kinds render identically; they differ only in the frame LABELS, which
  the parser supplies.
* :class:`TypeSpecGlyph` — Type Specialization: a SOLID bordered box plus a
  clean-room type icon in its selector.
"""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import Rect
from .selectable import SelectableStructureGlyph


class DisableGlyph(SelectableStructureGlyph):
    """Diagram / Conditional Disable — a dotted bordered box."""

    def __init__(self, *, border_color: str | None = None) -> None:
        self._apply_error_border(border_color)

    def draw_outline(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill="none",
            stroke=self.border_color or theme.struct_border,
            stroke_width=self.border_width,
            stroke_dasharray="1.5,2.5",
        )


class TypeSpecGlyph(SelectableStructureGlyph):
    """Type Specialization Structure — a solid bordered box (the base outline)
    plus a type icon in the selector's icon zone."""

    has_selector_icon = True

    def __init__(self, *, border_color: str | None = None) -> None:
        self._apply_error_border(border_color)

    def draw_selector_icon(self, backend: Backend, box: Rect, theme: Theme) -> None:
        """A clean-room 'adapts to type' glyph: a small square split into four
        quadrants in our own wire-family colors (float / int / string / bool),
        signalling the malleable/polymorphic type. Our composition from the lvkit
        palette — NOT NI artwork."""
        x1, y1, x2, y2 = box
        s = max(4.0, min(x2 - x1, y2 - y1) - 4.0)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        ix1, iy1 = cx - s / 2, cy - s / 2
        ix2, iy2 = ix1 + s, iy1 + s
        backend.rect(ix1, iy1, cx, cy, fill=theme.wire_float, stroke="none")  # TL
        backend.rect(cx, iy1, ix2, cy, fill=theme.wire_int, stroke="none")  # TR
        backend.rect(ix1, cy, cx, iy2, fill=theme.wire_string, stroke="none")  # BL
        backend.rect(cx, cy, ix2, iy2, fill=theme.wire_bool, stroke="none")  # BR
        backend.rect(
            ix1,
            iy1,
            ix2,
            iy2,
            fill="none",
            stroke=theme.struct_border,
            stroke_width=0.5,
        )
