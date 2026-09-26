"""Draw every top-level front-panel control: its own caption, then its
resolved value glyph. Recursion into a cluster's fields or an array's
element is the resolved glyph's OWN job (``ClusterConstantGlyph``/
``ArrayControlGlyph``, see ``controls/``), not this module's -- there is
nothing left here to clip or recurse into.
"""

from __future__ import annotations

from ..backend import Backend
from ..style import Theme
from .controls import draw_label, resolve_glyph, value_bounds
from .geometry import FPBox


def draw_front_panel(boxes: list[FPBox], backend: Backend, theme: Theme) -> None:
    for box in boxes:
        glyph = resolve_glyph(box.control, theme)
        bounds = value_bounds(glyph, box.bounds)
        draw_label(box.control, bounds, backend, theme)
        glyph.draw(backend, bounds, theme)
