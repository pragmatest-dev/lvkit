from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _CLUSTER_ARROW, draw_split_box


@dataclass(frozen=True)
class UnbundleGlyph:
    """The Unbundle primitive (split one cluster into N elements). Mirror of
    ``BundleGlyph`` matching LabVIEW's outline (NI docs slug ``unbundle``): a
    narrow LEFT cell (the input cluster side) with a right-pointing direction
    arrow, and a RIGHT area split into one row per element output."""

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
            symbol_side="left",
            fill_attr=self.fill_attr,
            stroke_attr=self.stroke_attr,
            text_attr=self.text_attr,
        )
