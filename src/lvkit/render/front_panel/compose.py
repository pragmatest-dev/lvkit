"""Draw every placed front-panel object: a control's own label or caption and
its resolved value glyph, a free label, a decoration, a Subpanel, a splitter bar and the
saved window's frame. Recursion into a cluster's fields or an array's element
is the resolved glyph's OWN job (``ClusterConstantGlyph``/``ArrayControlGlyph``,
see ``controls/``), not this module's.
"""

from __future__ import annotations

from ...parser.models import (
    ParsedFPControl,
    ParsedFPDecoration,
    ParsedFPLabel,
    ParsedFPSubPanel,
)
from ..backend import Backend
from ..style import Theme
from .controls import control_value_bounds, draw_label, resolve_glyph
from .controls.decoration import draw_decoration
from .controls.free_label import draw_free_label
from .controls.subpanel import draw_subpanel
from .geometry import FPBox, FPPaneBackground, FPSplitterBar, FPWindowFrame


def draw_front_panel(boxes: list[FPBox], backend: Backend, theme: Theme) -> None:
    for box in boxes:
        if box.clip is not None:
            backend.begin_group(clip=box.clip)
        _draw_box(box, backend, theme)
        if box.clip is not None:
            backend.end_group()


def _draw_box(box: FPBox, backend: Backend, theme: Theme) -> None:
    obj = box.obj
    if isinstance(obj, ParsedFPControl):
        glyph = resolve_glyph(obj, theme)
        bounds = control_value_bounds(obj, glyph, box.bounds)
        draw_label(obj, box.bounds, bounds, backend, theme)
        glyph.draw(backend, bounds, theme)
    elif isinstance(obj, ParsedFPLabel):
        draw_free_label(obj, box.bounds, backend, theme)
    elif isinstance(obj, ParsedFPDecoration):
        draw_decoration(obj, box.bounds, backend, theme)
    elif isinstance(obj, ParsedFPSubPanel):
        draw_subpanel(obj, box.bounds, backend, theme)
    elif isinstance(obj, FPPaneBackground):
        backend.rect(*box.bounds, fill=obj.color)
    elif isinstance(obj, FPSplitterBar):
        backend.rect(*box.bounds, fill=theme.struct_border)
    elif isinstance(obj, FPWindowFrame):
        x1, y1, x2, y2 = box.bounds
        backend.rect(
            x1, y1, x2, y2, fill="none", stroke=theme.struct_border, stroke_width=1.0
        )
