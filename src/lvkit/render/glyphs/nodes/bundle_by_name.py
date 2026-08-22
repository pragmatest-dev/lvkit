from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _CLUSTER_ARROW, draw_split_box


@dataclass(frozen=True)
class BundleByNameGlyph:
    """Bundle / Unbundle By Name (heap class ``nMux``): a box with one row per
    accessed field, each row LABELED with the field's NAME. Unlike the compact
    positional Bundle/Unbundle (``BundleGlyph``/``UnbundleGlyph``, terminals
    only), By Name shows the names — resolved from the wired cluster's type by
    the resolver. The small cluster (refnum) terminal is drawn separately at the
    node's corner; the named rows fill the box, one per ``list`` terminal in
    top-to-bottom order. Scales to the node's heap bounds (names left-aligned,
    truncated to fit)."""

    names: tuple[str, ...]
    bundling: bool = True  # True: Bundle By Name; False: Unbundle By Name
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        # Same skeleton as positional Bundle/Unbundle: a narrow cluster-direction
        # arrow cell (RIGHT when bundling, LEFT when unbundling) plus one row per
        # field — here the rows are LABELED with the field names. The arrow cell
        # is kept thin (~one row tall) since By-Name boxes grow with field count.
        x1, y1, x2, y2 = bounds
        rows = self.names or ("",)
        n = len(rows)
        arrow_w = min((x2 - x1) * 0.33, max(10.0, (y2 - y1) / n))
        draw_split_box(
            backend,
            bounds,
            theme,
            symbol=_CLUSTER_ARROW,
            num_cells=n,
            symbol_side="right" if self.bundling else "left",
            fill_attr=self.fill_attr,
            stroke_attr=self.stroke_attr,
            text_attr=self.text_attr,
            cell_labels=rows,
            sym_w=arrow_w,
        )
