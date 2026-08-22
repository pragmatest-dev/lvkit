from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .wrapped_box import WrappedBoxGlyph


@dataclass(frozen=True)
class LocalVariableGlyph:
    """A Local/Global Variable node: a bordered box with the referenced
    control's NAME, marked by a small filled right-pointing triangle badge —
    the marker that tells it apart from a same-shaped constant/subVI box (a
    constant has no badge). Grounded in NI's public "Local Variable"
    function-reference node image (a ``▶`` glyph beside the control name); the
    triangle is our own clean-room shape, not NI artwork.

    The badge sits on the DATAFLOW side: a READ variable is a source (data
    leaves to the right), so its ▶ is right-justified on the output edge; a
    WRITE variable is a sink (data enters from the left), so its ▶ is on the
    left input edge — that side IS the read/write signal. Both get a BOLD border
    so a local variable stands out from a plain constant/subVI box.

    ``border_color`` (a literal color, like ``ConstantGlyph.color``) is the
    variable's DATA-TYPE wire color — LabVIEW colors a local variable's border
    by its type (boolean green, string pink, ...). It's None only when the type
    is unresolved, falling back to the neutral ``stroke_attr``."""

    label: str
    is_write: bool = False
    border_color: str | None = None
    fill_attr: str = "localvar_fill"
    stroke_attr: str = "localvar_stroke"
    text_attr: str = "localvar_text"
    max_lines: int = 2
    text_size: float = 7.0

    _STROKE_W = 2.5  # bold border for both read and write

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = self.border_color or getattr(theme, self.stroke_attr)
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=stroke,
            stroke_width=self._STROKE_W,
        )
        # A small filled right-pointing triangle on the dataflow side: right
        # (output) edge for a read, left (input) edge for a write.
        pad = 2.0
        cy = (y1 + y2) / 2
        tri_h = min(8.0, (y2 - y1) - 2 * pad)
        left_pad = right_pad = 0.0
        if tri_h >= 4.0 and (x2 - x1) > 2 * pad + 6.0:
            tw = tri_h * 0.72
            fill = getattr(theme, self.text_attr)
            if self.is_write:
                tx = x1 + pad + 1.0  # left input edge
                left_pad = pad + 1.0 + tw
            else:
                tx = x2 - pad - 1.0 - tw  # right output edge
                right_pad = pad + 1.0 + tw
            backend.polygon(
                [(tx, cy - tri_h / 2), (tx + tw, cy), (tx, cy + tri_h / 2)],
                fill=fill,
                stroke=None,
            )
        # Name fills the width remaining beside the badge.
        text_glyph = WrappedBoxGlyph(
            self.label,
            self.fill_attr,
            self.stroke_attr,
            max_lines=self.max_lines,
            text_size=self.text_size,
            text_attr=self.text_attr,
        )
        text_glyph.draw_wrapped_text(
            backend,
            (x1 + left_pad, y1, x2 - right_pad, y2),
            theme,
        )
