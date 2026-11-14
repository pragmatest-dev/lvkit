"""The value glyph for a ``stdPict`` (Picture) control."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme

# Below this box size the icon's own strokes would overlap and read as
# noise -- same "too small, skip the detail" convention as
# refnum_glyph._MINI_MAX_W/_MINI_MAX_H, sized to the icon's own geometry
# below rather than a corpus-measured control.
_MIN_ICON_SIZE = 12.0


@dataclass(frozen=True)
class PictureControlGlyph:
    """A framed placeholder for a Picture control. LabVIEW's own saved value
    is NOT a raster -- checked against 5 real corpus ``DefaultData`` blobs:
    none start with a PNG/BMP magic number, and all match a small-integer,
    length-prefixed byte pattern instead. That the specific bytes are a
    line/rect/fill DRAWING-COMMAND opcode stream is LabVIEW general
    knowledge, not itself decoded here -- either way, decoding it is out of
    scope, so this draws a generic "picture" icon (a frame, a small circle,
    a mountain-horizon polygon -- the common cross-platform image-placeholder
    convention) rather than fake content or a bare unlabeled box. This is an
    unverified generic convention, not checked against any specific vendor's
    icon (contrast ``refnum_glyph._kind_generic``, whose fallback shape IS
    cited against NI's own public docs)."""

    border_color: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=theme.fp_value_fill, stroke=self.border_color, stroke_width=1.0,
        )
        w, h = x2 - x1, y2 - y1
        if w < _MIN_ICON_SIZE or h < _MIN_ICON_SIZE:
            return  # too small to hold the icon legibly
        pad = min(w, h) * 0.16
        ix1, iy1, ix2, iy2 = x1 + pad, y1 + pad, x2 - pad, y2 - pad
        iw, ih = ix2 - ix1, iy2 - iy1
        color = theme.pane_type_text
        r = min(iw, ih) * 0.14
        backend.circle(ix1 + iw * 0.28, iy1 + ih * 0.28, r, fill="none", stroke=color,
                        stroke_width=1.0)
        backend.polygon(
            [
                (ix1, iy2),
                (ix1 + iw * 0.32, iy1 + ih * 0.55),
                (ix1 + iw * 0.55, iy1 + ih * 0.78),
                (ix1 + iw * 0.72, iy1 + ih * 0.42),
                (ix2, iy2),
            ],
            fill="none", stroke=color, stroke_width=1.0,
        )
