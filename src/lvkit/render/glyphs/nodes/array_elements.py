"""The array's element column: a clipped, scrollable column of cells inside a
viewport, plus the ``lv-array`` carrier the array controller JS reads.

Shared by the block-diagram array constant and the front-panel array control.
Every visible row is a whole cell; rows past the array's end draw the element
type's own default-valued glyph under the disabled wash, never a blank rect,
and an empty array still shows its row 0.
"""

from __future__ import annotations

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import Glyph

# The default element-cell height when the element has no real geometry.
DEFAULT_CELL_H = 18.0


def visible_rows(viewport: Rect, cell_h: float) -> int:
    """Whole rows that fit the viewport's height -- at least one."""
    _, vy1, _, vy2 = viewport
    return max(1, int((vy2 - vy1) // cell_h))


def draw_array_elements(
    backend: Backend,
    viewport: Rect,
    theme: Theme,
    *,
    struct_uid: str,
    elements: tuple[Glyph, ...],
    default_element: Glyph | None,
    cell_h: float,
    cell_w: float | None,
) -> int:
    """Draw the column and its carrier; returns the visible-row count."""
    vx1, vy1, vx2, vy2 = viewport
    visible = visible_rows(viewport, cell_h)
    total = len(elements)

    # A FIXED clip viewport (outer group) holding a TRANSLATABLE column
    # (inner ``lv-array-col``): every element cell at its natural row, plus up
    # to ``visible - 1`` past-end rows so scrolling near the end reveals the
    # "unset" cells. The controller JS translates the inner group by
    # ``-index * cell_h`` so element[index] lands at the viewport top (the clip
    # must stay on the OUTER group, or it would scroll too). With no JS it
    # shows rows [0, visible).
    backend.begin_group(clip=(vx1, vy1, vx2, vy2))
    backend.begin_group(cls="lv-array-col", data={"lv-struct": struct_uid})
    cell_right = vx2 - 1.0
    if cell_w is not None:
        cell_right = min(vx2, vx1 + cell_w) - 1.0
    row_count = max(total, 1) + max(0, visible - 1)
    for i in range(row_count):
        cy1 = vy1 + i * cell_h
        cy2 = cy1 + cell_h
        cell = (vx1 + 1.0, cy1 + 1.0, cell_right, cy2 - 1.0)
        if i < total:
            elements[i].draw(backend, cell, theme)
        elif default_element is not None:
            default_element.draw(backend, cell, theme)
            backend.begin_group(cls="lv-disabled-mask")
            backend.rect(*cell, fill=theme.disabled_mask)
            backend.end_group()
        else:
            backend.rect(*cell, fill=theme.fp_panel, stroke="none")
        if i + 1 < row_count:
            backend.line(
                vx1, cy2, vx2, cy2, stroke=theme.struct_border, stroke_width=0.4
            )
    backend.end_group()  # lv-array-col (translatable)
    backend.end_group()  # clip viewport (fixed)

    backend.begin_group(
        cls="lv-array",
        data={
            "lv-struct": struct_uid,
            "lv-len": str(total),
            "lv-visible": str(visible),
            "lv-cellh": str(cell_h),
        },
    )
    backend.end_group()
    return visible
