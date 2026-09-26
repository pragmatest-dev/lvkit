"""Front-panel control glyphs: resolve each ``ParsedFPControl`` to a plain
``Glyph`` (``draw(backend, bounds, theme)`` -- the same protocol every
block-diagram glyph implements), one module per control.

Chrome the block-diagram renderer already solves is REUSED, not redrawn: a
cluster is a ``ClusterConstantGlyph``, a string a ``ConstantGlyph``, a path a
``PathGlyph``. Genuinely new here is only the front-panel CONTROL chrome a
block-diagram CONSTANT never needs -- a numeric control's spinner, a boolean's
slide switch, an enum's dropdown chevron, and the array control's own frame,
index selector and scrollbar (``array.py``).
"""

from __future__ import annotations

from .base import ControlGlyph, control_value_bounds, value_bounds
from .label import draw_label
from .resolve import resolve_glyph

__all__ = [
    "ControlGlyph",
    "control_value_bounds",
    "draw_label",
    "resolve_glyph",
    "value_bounds",
]
