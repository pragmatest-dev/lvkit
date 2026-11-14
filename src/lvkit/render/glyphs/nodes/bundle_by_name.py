from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import fit_label

# Fallback width for the cluster (aggregate) column when the node carries no
# real aggregate ``termBounds`` to size it from — otherwise the column spans the
# terminal's actual heap rect (see ``draw._draw_bundle_by_name``).
_CLUSTER_COL_W = 8.0


def _draw_bundle_arrow(
    backend: Backend,
    x1: float,
    x2: float,
    y1: float,
    y2: float,
    color: str,
) -> None:
    """A small RIGHTWARD arrow (a shaft plus a filled triangular head) centered
    in the aggregate's cluster column. Both Bundle and Unbundle point right —
    data assembles/extracts left-to-right. The shaft is what distinguishes it
    from a bare triangle (which reads as a media "play" button); scaled to the
    narrow column it stays a small, clean arrow."""
    cy = (y1 + y2) / 2
    acx = (x1 + x2) / 2
    aw = (x2 - x1) * 0.72  # arrow spans most of the column width
    ax1, ax2 = acx - aw / 2, acx + aw / 2
    head = aw * 0.6  # 40% shaft, 60% triangular head
    hh = head * 0.75  # head half-height (full head height = 2*hh)
    sw = hh  # shaft thickness tracks the head height
    backend.line(ax1, cy, ax2 - head, cy, stroke=color, stroke_width=sw)
    backend.polygon(
        [(ax2 - head, cy - hh), (ax2, cy), (ax2 - head, cy + hh)],
        fill=color,
    )


@dataclass(frozen=True)
class BundleByNameGlyph:
    """Bundle / Unbundle By Name (heap class ``nMux``): a stack of bordered
    field-NAME cells beside the aggregate CLUSTER column. For Bundle the cluster
    column is on the RIGHT — drawn as two equal columns matching LabVIEW's
    connector pane: the INTERIOR (left) takes the input source cluster (entering
    at the top), the EDGE (right) the assembled output cluster (exiting the
    right). For Unbundle the single cluster column is on the LEFT. Name cells
    are filled with the
    diagram background (they read as data cells, like LabVIEW), the cluster
    column with the aggregate fill. Each field's name fills one cell,
    top-to-bottom in ``list``-terminal order; scales to the node's heap bounds."""

    names: tuple[str, ...]
    bundling: bool = True  # True: Bundle By Name; False: Unbundle By Name
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(
        self,
        backend: Backend,
        bounds: Rect,
        theme: Theme,
        *,
        agg_x1: float | None = None,
        agg_x2: float | None = None,
    ) -> None:
        x1, y1, x2, y2 = bounds
        rows = self.names or ("",)
        n = len(rows)
        col_fill = getattr(theme, self.fill_attr)
        stroke = getattr(theme, self.stroke_attr)
        text = getattr(theme, self.text_attr)
        # Name cells read as data: the diagram background, not the node fill.
        cell_fill = theme.canvas

        # Aggregate column spans the terminal's real heap rect when the caller
        # supplies it; otherwise fall back to a fixed sliver on the right/left.
        if agg_x1 is not None and agg_x2 is not None and agg_x2 > agg_x1:
            col_x1, col_x2 = agg_x1, agg_x2
        elif self.bundling:
            w = min(_CLUSTER_COL_W, (x2 - x1) * 0.2)
            col_x1, col_x2 = x2 - w, x2
        else:
            w = min(_CLUSTER_COL_W, (x2 - x1) * 0.2)
            col_x1, col_x2 = x1, x1 + w

        if self.bundling:  # cluster column on the RIGHT
            cell_x1, cell_x2 = x1, col_x1
        else:  # Unbundle: cluster column on the LEFT
            cell_x1, cell_x2 = col_x2, x2

        # Field-name cells: one bordered box per field, stacked.
        row_h = (y2 - y1) / n
        lsize = max(6.0, min(9.0, row_h * 0.62))
        for i, name in enumerate(rows):
            ry1 = y1 + i * row_h
            ry2 = ry1 + row_h
            backend.rect(
                cell_x1, ry1, cell_x2, ry2, fill=cell_fill, stroke=stroke,
                stroke_width=1.0,
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

        # Aggregate cluster column.
        backend.rect(
            col_x1, y1, col_x2, y2, fill=col_fill, stroke=stroke, stroke_width=1.0
        )
        mid_x = (col_x1 + col_x2) / 2
        if self.bundling:
            # Two equal columns (LabVIEW's connector pane): the INTERIOR (left)
            # takes the input source cluster entering at the top, the EDGE
            # (right) the assembled output exiting the right — the arrow sits in
            # that edge column.
            backend.line(mid_x, y1, mid_x, y2, stroke=stroke, stroke_width=1.0)
            _draw_bundle_arrow(backend, mid_x, col_x2, y1, y2, text)
        else:
            _draw_bundle_arrow(backend, col_x1, col_x2, y1, y2, text)
