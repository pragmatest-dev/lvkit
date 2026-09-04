"""``ArrayConstantGlyph`` — a LabVIEW array constant.

LabVIEW draws an array constant as a per-dimension INDEX control on the left
(``▲``/``▼`` to change the index) plus a scrolling column of ELEMENT cells: the
INDEXED element sits at the top and a whole number of further rows fill the
bounding box. Elements past the array's end are greyed (unset). The index is
clamped to the array — you can't page before element 0 or past the last.

The glyph stays PURE (scales to the given ``bounds`` — the constant's real heap
box). The index is INTERACTIVE in the viewer: the ``lv-array`` carrier + the
``lv-array-col`` translatable column are read by the array controller JS (a
sibling of the case/sequence frame controller), which scrolls the column and
updates the index readout on ``▲``/``▼``. With no JS the glyph shows index 0.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import Glyph, fit_label

# One element row + the index box — LabVIEW's element-cell height. The box is the
# real developer-sized heap bounds, so the visible-row count falls out as
# ``floor(viewport_height / _CELL_H)`` — a whole number of elements, as LabVIEW
# always shows.
_CELL_H = 18.0
_INDEX_W = 22.0  # width of the index-control column (left of the elements)
_INDEX_H = 16.0  # height of one dimension's index box
_PAD = 2.0


@dataclass(frozen=True)
class ArrayConstantGlyph:
    """An array constant: an index control (one box per dimension) + a clipped,
    scrollable column of the element values' own glyphs. ``elements`` is one
    composed glyph per array value (built by the resolver from the element
    type), so an array of clusters composes each cluster into its cell."""

    elements: tuple[Glyph, ...]
    element_color: str
    struct_uid: str
    dimensions: int = 1

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=theme.const_fill,
            stroke=self.element_color,
            stroke_width=1.0,
        )
        n_idx = max(1, self.dimensions)
        idx_right = x1 + _INDEX_W
        for d in range(n_idx):
            iy1 = y1 + _PAD + d * (_INDEX_H + 1.0)
            iy2 = min(iy1 + _INDEX_H, y2 - _PAD)
            if iy2 - iy1 < 6.0:
                break
            self._draw_index_box(
                backend, (x1 + _PAD, iy1, idx_right - _PAD, iy2), theme
            )

        # Element viewport, to the right of the index column.
        vx1, vy1, vx2, vy2 = idx_right, y1 + _PAD, x2 - _PAD, y2 - _PAD
        if vx2 - vx1 < 6.0 or vy2 - vy1 < 6.0:
            return
        visible = max(1, int((vy2 - vy1) // _CELL_H))
        total = len(self.elements)

        # A FIXED clip viewport (outer group) holding a TRANSLATABLE column
        # (inner ``lv-array-col``): every element cell at its natural row, plus
        # up to ``visible - 1`` greyed past-end rows so scrolling near the end
        # reveals the "unset" cells. The controller JS translates the inner group
        # by ``-index * _CELL_H`` so element[index] lands at the viewport top
        # (the clip must stay on the OUTER group, or it would scroll too). With
        # no JS it shows rows [0, visible).
        backend.begin_group(clip=(vx1, vy1, vx2, vy2))
        backend.begin_group(
            cls="lv-array-col",
            data={"lv-struct": self.struct_uid},
        )
        for i in range(total + max(0, visible - 1)):
            cy1 = vy1 + i * _CELL_H
            cy2 = cy1 + _CELL_H
            cell = (vx1 + 1.0, cy1 + 1.0, vx2 - 1.0, cy2 - 1.0)
            if i < total:
                self.elements[i].draw(backend, cell, theme)
            else:
                # Past the array end: a greyed, disabled cell.
                backend.rect(*cell, fill=theme.fp_panel, stroke="none")
            if i + 1 < total + max(0, visible - 1):
                backend.line(
                    vx1, cy2, vx2, cy2, stroke=theme.struct_border, stroke_width=0.4
                )
        backend.end_group()  # lv-array-col (translatable)
        backend.end_group()  # clip viewport (fixed)

        # The carrier the array controller reads: array length, visible-row
        # count, and the per-row height it scrolls by.
        backend.begin_group(
            cls="lv-array",
            data={
                "lv-struct": self.struct_uid,
                "lv-len": str(total),
                "lv-visible": str(visible),
                "lv-cellh": str(_CELL_H),
            },
        )
        backend.end_group()

    def _draw_index_box(
        self, backend: Backend, box: Rect, theme: Theme
    ) -> None:
        """One dimension's index control: a box with ``▲``/``▼`` decrement /
        increment click targets on the left and the current index readout on the
        right (updated live by the controller; ``0`` with no JS)."""
        ix1, iy1, ix2, iy2 = box
        backend.rect(
            ix1, iy1, ix2, iy2,
            fill=theme.case_bar_fill,
            stroke=theme.struct_border,
            stroke_width=0.75,
        )
        arrow_w = 8.0
        mid = (iy1 + iy2) / 2
        # ▲ (top) = index UP / next (index + 1); ▼ (bottom) = index down / prev.
        backend.begin_group(
            cls="lv-selector lv-clickable",
            data={"lv-action": "next", "lv-struct": self.struct_uid},
        )
        backend.rect(ix1, iy1, ix1 + arrow_w, mid, fill="transparent", stroke="none")
        backend.polygon(
            [
                (ix1 + arrow_w / 2, iy1 + 2.5),
                (ix1 + 1.5, mid - 1.5),
                (ix1 + arrow_w - 1.5, mid - 1.5),
            ],
            fill=theme.case_bar_text,
        )
        backend.end_group()
        backend.begin_group(
            cls="lv-selector lv-clickable",
            data={"lv-action": "prev", "lv-struct": self.struct_uid},
        )
        backend.rect(ix1, mid, ix1 + arrow_w, iy2, fill="transparent", stroke="none")
        backend.polygon(
            [
                (ix1 + 1.5, mid + 1.5),
                (ix1 + arrow_w - 1.5, mid + 1.5),
                (ix1 + arrow_w / 2, iy2 - 2.5),
            ],
            fill=theme.case_bar_text,
        )
        backend.end_group()
        # The live index readout (JS sets textContent; 0 by default).
        size = min(9.0, (iy2 - iy1) * 0.7)
        label = fit_label("0", (ix2 - (ix1 + arrow_w)) - 2.0, backend, size)
        backend.begin_group(
            cls="lv-array-index",
            data={"lv-struct": self.struct_uid},
        )
        backend.text(
            (ix1 + arrow_w + ix2) / 2,
            mid + size * 0.34,
            label,
            size,
            fill=theme.case_bar_text,
        )
        backend.end_group()
