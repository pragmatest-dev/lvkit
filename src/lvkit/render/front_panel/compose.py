"""Recursive front-panel draw: for each placed ``FPBox``, draw its own
chrome then recurse into its children — the same "container chrome first,
recurse into already-placed children" split
``composite.py::StructureObject.draw`` uses for block-diagram structures,
just without a clip group (a front panel's children are already placed
strictly inside their parent's box by ``geometry.py``, so there's nothing to
clip away).
"""

from __future__ import annotations

from ..backend import Backend
from ..style import Theme
from .controls import COMPOSITE_CONTROL_TYPES, resolve_control_glyph
from .geometry import FPBox


def draw_box(box: FPBox, backend: Backend, theme: Theme) -> None:
    draw = resolve_control_glyph(box.control.control_type)
    draw(box, backend, theme)
    if box.control.control_type in COMPOSITE_CONTROL_TYPES:
        for child in box.children:
            draw_box(child, backend, theme)


def draw_front_panel(boxes: list[FPBox], backend: Backend, theme: Theme) -> None:
    for box in boxes:
        draw_box(box, backend, theme)
