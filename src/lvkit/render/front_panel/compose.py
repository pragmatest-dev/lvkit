"""Draw every top-level front-panel control: its own caption, then its
resolved value glyph. Recursion into a cluster's fields or an array's
element is now the resolved glyph's OWN job (``ClusterConstantGlyph``/
``ArrayConstantGlyph``, see ``controls.py``), not this module's -- there is
nothing left here to clip or recurse into.
"""

from __future__ import annotations

from ..backend import Backend
from ..style import Theme
from .controls import draw_label, resolve_glyph
from .geometry import FPBox


def draw_front_panel(boxes: list[FPBox], backend: Backend, theme: Theme) -> None:
    for box in boxes:
        draw_label(box.control.name, box.bounds, backend, theme)
        resolve_glyph(box.control, theme).draw(backend, box.bounds, theme)
