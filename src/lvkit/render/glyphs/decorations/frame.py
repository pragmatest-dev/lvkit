"""``FrameGlyph`` — LabVIEW's "Flat Frame" decoration: a double-ruled band box."""

from __future__ import annotations

from ...backend import Backend
from ...style import Theme
from .base import THIN_W, DecorationGlyph, Rect

# Width (px) of the frame's border BAND: a black outer rule, an opaque white
# fill, and a black inner rule — LabVIEW's picture-frame double border (issue
# #32 reference). The band is opaque (it occludes what it crosses); the frame's
# INTERIOR stays transparent so it can enclose nodes without hiding them.
_BAND_W = 3.0
_FILL = "#ffffff"


class FrameGlyph(DecorationGlyph):
    """A flat rectangular frame: an opaque white border band (``_BAND_W`` wide)
    ruled black on its outer and inner edges, interior transparent. The bounds
    are the OUTER edge of the frame."""

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w = min(_BAND_W, (x2 - x1) / 2, (y2 - y1) / 2)
        # White band on the four margins (opaque, so it occludes what it crosses).
        backend.rect(x1, y1, x2, y1 + w, fill=_FILL, stroke=None)  # top
        backend.rect(x1, y2 - w, x2, y2, fill=_FILL, stroke=None)  # bottom
        backend.rect(x1, y1 + w, x1 + w, y2 - w, fill=_FILL, stroke=None)  # left
        backend.rect(x2 - w, y1 + w, x2, y2 - w, fill=_FILL, stroke=None)  # right
        # Black rules: outer edge on the bounds (pulled in half a stroke so the
        # outer edge lands on the box), inner edge on the band's inner boundary.
        color = theme.struct_border
        h = THIN_W / 2
        backend.rect(
            x1 + h,
            y1 + h,
            x2 - h,
            y2 - h,
            fill="none",
            stroke=color,
            stroke_width=THIN_W,
        )
        backend.rect(
            x1 + w,
            y1 + w,
            x2 - w,
            y2 - w,
            fill="none",
            stroke=color,
            stroke_width=THIN_W,
        )
