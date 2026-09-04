from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import fit_label

# Width of the cluster (aggregate) column — the narrow column holding the
# direction arrow, on the RIGHT for Bundle, LEFT for Unbundle.
_CLUSTER_COL_W = 13.0


@dataclass(frozen=True)
class BundleByNameGlyph:
    """Bundle / Unbundle By Name (heap class ``nMux``): a stack of bordered
    field-NAME cells beside a narrow CLUSTER column that carries the direction
    arrow. For Bundle the cluster column is on the RIGHT (the aggregate exits
    there, its "input cluster" attaching at the column top — see
    ``scene._bundle_agg_centers``); for Unbundle it is on the LEFT. Each field's
    name fills one cell, top-to-bottom in ``list``-terminal order. Scales to the
    node's heap bounds (names left-aligned, truncated to fit)."""

    names: tuple[str, ...]
    bundling: bool = True  # True: Bundle By Name; False: Unbundle By Name
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        rows = self.names or ("",)
        n = len(rows)
        fill = getattr(theme, self.fill_attr)
        stroke = getattr(theme, self.stroke_attr)
        text = getattr(theme, self.text_attr)

        col_w = min(_CLUSTER_COL_W, (x2 - x1) * 0.4)
        if self.bundling:  # cluster column on the RIGHT
            cell_x1, cell_x2 = x1, x2 - col_w
            col_x1, col_x2 = x2 - col_w, x2
            points_right = True
        else:  # Unbundle: cluster column on the LEFT
            col_x1, col_x2 = x1, x1 + col_w
            cell_x1, cell_x2 = x1 + col_w, x2
            points_right = False

        # Field-name cells: one bordered box per field, stacked.
        row_h = (y2 - y1) / n
        lsize = max(6.0, min(9.0, row_h * 0.62))
        for i, name in enumerate(rows):
            ry1 = y1 + i * row_h
            ry2 = ry1 + row_h
            backend.rect(
                cell_x1, ry1, cell_x2, ry2, fill=fill, stroke=stroke, stroke_width=1.0
            )
            label = fit_label(name, (cell_x2 - cell_x1) - 4.0, backend, lsize)
            backend.text(
                cell_x1 + 2.0,
                (ry1 + ry2) / 2 + lsize * 0.34,
                label,
                lsize,
                anchor="start",
                fill=text,
            )

        # Cluster column + the direction arrow (a filled triangle) at mid — the
        # aggregate OUTPUT sits at mid; the INPUT attaches at the column top.
        backend.rect(
            col_x1, y1, col_x2, y2, fill=fill, stroke=stroke, stroke_width=1.0
        )
        mid_y = (y1 + y2) / 2
        acx = (col_x1 + col_x2) / 2
        aw = (col_x2 - col_x1) * 0.62
        ah = min(row_h * 0.5, 5.0)
        back_x = acx - aw / 2 if points_right else acx + aw / 2
        tip_x = acx + aw / 2 if points_right else acx - aw / 2
        backend.polygon(
            [
                (back_x, mid_y - ah),
                (tip_x, mid_y),
                (back_x, mid_y + ah),
            ],
            fill=text,
        )
