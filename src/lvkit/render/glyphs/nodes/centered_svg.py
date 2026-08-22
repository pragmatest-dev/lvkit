from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


@dataclass(frozen=True)
class CenteredSvgGlyph:
    """A raster VI icon (an ``_ICON.png`` vectorized to SVG), scaled to FILL
    its box, CENTERED, aspect preserved. Constructed only by
    ``nodes._vectorized_icon`` — real icons from disk, never procedural
    primitive art — so filling here can't stretch a drawn primitive."""

    fragment: str
    natural: tuple[int, int]  # (width, height) in the SVG's own viewBox units

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        bw, bh = x2 - x1, y2 - y1
        nw, nh = self.natural
        if nw <= 0 or nh <= 0:
            return
        # Fill the box (aspect preserved) — no 1.0 cap, so a small native icon
        # scales UP to its box instead of floating tiny in whitespace.
        scale = min(bw / nw, bh / nh)
        w, h = nw * scale, nh * scale
        ox, oy = x1 + (bw - w) / 2, y1 + (bh - h) / 2
        backend.raw_svg(self.fragment, ox, oy, w, h, viewbox=(nw, nh))
