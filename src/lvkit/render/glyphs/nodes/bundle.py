from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _CLUSTER_ARROW, draw_split_box


@dataclass(frozen=True)
class BundleGlyph:
    """The Bundle primitive (assemble N elements into one cluster). Original
    clean-room glyph matching LabVIEW's outline (NI docs slug ``bundle``): a
    box whose LEFT area is split into one row per element input, and whose
    narrow RIGHT cell (the output cluster side) carries a right-pointing
    direction arrow. Drawn via the shared ``draw_split_box`` skeleton."""

    num_fields: int = 1
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        draw_split_box(
            backend,
            bounds,
            theme,
            symbol=_CLUSTER_ARROW,
            num_cells=self.num_fields,
            symbol_side="right",
            fill_attr=self.fill_attr,
            stroke_attr=self.stroke_attr,
            text_attr=self.text_attr,
        )
