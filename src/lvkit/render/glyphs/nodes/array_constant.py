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

The index selector, element column and scrollbar are the shared pieces in
``index_selector``/``array_elements``/``array_scrollbar``; this glyph decides
where a block-diagram constant puts them.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .array_elements import DEFAULT_CELL_H, draw_array_elements
from .array_scrollbar import draw_array_scrollbar
from .base import Glyph
from .index_selector import draw_index_selector
from .local_rect import scale_local

_INDEX_W = 22.0  # width of the index-control column (left of the elements)
_INDEX_H = 16.0  # height of one dimension's index box
_PAD = 2.0


@dataclass(frozen=True)
class ArrayConstantGlyph:
    """An array constant: an index control (one box per dimension) + a clipped,
    scrollable column of the element values' own glyphs. ``elements`` is one
    composed glyph per array value (built by the resolver from the element
    type), so an array of clusters composes each cluster into its cell.

    ``cell_h``/``cell_w`` override the default synthetic row size — set when
    the element is a cluster with REAL heap geometry (``ClusterGeom``), so
    every visible row draws that cluster at its own real, undistorted size
    (arrays are homogeneous: one real shape serves every row) rather than
    stretched into a guessed row height. ``cell_w`` clips the cell's width to
    that real size (left-anchored in the viewport, never stretched to fill
    it); ``None`` (the default, every non-cluster element) keeps a fixed
    default-height row filling the full viewport width.

    ``default_element`` is the element TYPE's own glyph at its type-default
    value (``value=None`` — the same "unset" convention every leaf/cluster
    field already draws; see ``nodes._leaf_const_glyph``/
    ``_cluster_value_glyph``) — LabVIEW shows a DISABLED default-valued
    element for every unset/past-end row, never a blank rect, and an EMPTY
    array (``elements == ()``) shows exactly one such row at index 0. Drawn
    with the existing ``lv-disabled-mask`` wash (``theme.disabled_mask`` at
    ~0.5 opacity — the same translucent-grey convention a disabled
    subdiagram frame already uses, see ``composite.py``), not a new style.
    ``None`` (an older caller, or an element type with no default glyph)
    falls back to a flat grey rect.

    ``viewport_local``/``scrollbar_local`` are the heap's own elements-viewport
    and scrollbar-track rects (``partID`` 28 / 39), relative to this constant's
    own (0, 0) at ``native_size`` -- mapped onto ``bounds`` by one uniform
    scale. ``None`` falls back to a synthetic carve-out beside the index
    column and no scrollbar. ``show_index`` is the heap's per-constant "Show
    Index" toggle."""

    elements: tuple[Glyph, ...]
    element_color: str
    struct_uid: str
    dimensions: int = 1
    cell_h: float | None = None
    cell_w: float | None = None
    default_element: Glyph | None = None
    viewport_local: Rect | None = None
    scrollbar_local: Rect | None = None
    native_size: tuple[float, float] | None = None
    show_index: bool = True

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=theme.const_fill,
            stroke=self.element_color,
            stroke_width=1.0,
        )
        idx_right = x1
        if self.show_index:
            idx_right = x1 + _INDEX_W
            for d in range(max(1, self.dimensions)):
                iy1 = y1 + _PAD + d * (_INDEX_H + 1.0)
                iy2 = min(iy1 + _INDEX_H, y2 - _PAD)
                if iy2 - iy1 < 6.0:
                    break
                draw_index_selector(
                    backend,
                    (x1 + _PAD, iy1, idx_right - _PAD, iy2),
                    theme,
                    self.struct_uid,
                )

        viewport = scale_local(self.viewport_local, self.native_size, bounds)
        if viewport is None:
            viewport = (idx_right, y1 + _PAD, x2 - _PAD, y2 - _PAD)
        vx1, vy1, vx2, vy2 = viewport
        if vx2 - vx1 < 6.0 or vy2 - vy1 < 6.0:
            return
        visible = draw_array_elements(
            backend,
            viewport,
            theme,
            struct_uid=self.struct_uid,
            elements=self.elements,
            default_element=self.default_element,
            cell_h=self.cell_h if self.cell_h is not None else DEFAULT_CELL_H,
            cell_w=self.cell_w,
        )
        scrollbar = scale_local(self.scrollbar_local, self.native_size, bounds)
        if scrollbar is not None:
            draw_array_scrollbar(
                backend, scrollbar, theme, self.struct_uid,
                len(self.elements), visible,
            )
