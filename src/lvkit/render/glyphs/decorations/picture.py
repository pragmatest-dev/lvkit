"""``PictureGlyph`` — an embedded picture decoration (a POSITIVE ``ImageResID``
naming a DSIM/PNG resource section, not a Decorations-palette shape id)."""

from __future__ import annotations

import base64

from ...backend import Backend
from ...style import Theme
from .base import DecorationGlyph, Rect


class PictureGlyph(DecorationGlyph):
    """Draws the VI's own embedded PNG — carved from its DSIM resource section
    by ``parser.image_resources.carve_png`` — scaled to the decoration's
    on-diagram bounds. LabVIEW stretches the picture to whatever box the
    developer sized it to on the diagram, so the PNG's intrinsic dimensions
    (which can differ from the box — the issue #82 repro embeds a 1402x1122
    picture in an 803x643 box) are irrelevant to the drawn footprint. Unlike a
    connector-pane icon, this is arbitrary artwork, not pixel art, so it is
    drawn WITHOUT the icon layer's ``pixelated`` scaling hint (smooth-scaled)."""

    def __init__(self, png: bytes) -> None:
        self._href = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.image(self._href, x1, y1, x2 - x1, y2 - y1, pixelated=False)
