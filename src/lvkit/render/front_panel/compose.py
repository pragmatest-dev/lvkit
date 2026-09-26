"""Draw every top-level front-panel control: its own label or caption, then its
resolved value glyph. Recursion into a cluster's fields or an array's
element is the resolved glyph's OWN job (``ClusterConstantGlyph``/
``ArrayControlGlyph``, see ``controls/``), not this module's -- there is
nothing left here to clip or recurse into.
"""

from __future__ import annotations

from ..backend import Backend
from ..style import Theme
from .controls import control_value_bounds, draw_label, resolve_glyph
from .geometry import FPBox


def draw_front_panel(boxes: list[FPBox], backend: Backend, theme: Theme) -> None:
    for box in boxes:
        glyph = resolve_glyph(box.control, theme)
        bounds = control_value_bounds(box.control, glyph, box.bounds)
        draw_label(box.control, box.bounds, bounds, backend, theme)
        glyph.draw(backend, bounds, theme)
