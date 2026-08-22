from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _draw_arrow, _draw_node_tile


@dataclass(frozen=True)
class InPlaceElementGlyph:
    """The In Place Element Structure's generic pass-through border node — a
    WHOLE value crossing the structure boundary unchanged (no field split),
    used for the variant-match / array / data-value-reference decompose-
    recompose pairs that aren't a real Bundle/Unbundle (see
    ``decomposeMatchNode`` — the corpus shows exactly 2 terminals and no
    dcoAgg/dcoList field shape at all, unlike ``decomposeClusterNode``). The
    input-side and output-side border node draw IDENTICALLY: a small square
    tile with a single rightward arrow. Clean-room — there is no NI artwork
    to trace here, just the drawn intent "a value flows through"."""

    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        stroke = getattr(theme, self.stroke_attr)
        cy = (y1 + y2) / 2
        _draw_node_tile(backend, bounds, theme, self.fill_attr, self.stroke_attr)
        sw = max(1.0, min(w, h) * 0.09)
        arrow_x1, arrow_x2 = x1 + w * 0.22, x1 + w * 0.78
        _draw_arrow(
            backend,
            arrow_x1,
            arrow_x2,
            cy,
            stroke=stroke,
            stroke_width=sw,
            head_len=max(3.0, w * 0.18),
            head_half_h=max(2.0, h * 0.24),
        )
