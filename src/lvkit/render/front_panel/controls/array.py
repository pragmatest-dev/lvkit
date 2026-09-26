"""The front-panel array control and its own drawing rules.

A front-panel array is drawn from the heap's own parts, not a synthetic
layout:

- the FRAME (``partID`` 9) surrounds the elements and scrollbar only -- the
  index selector is NOT inside it;
- the INDEX selector (``partID`` 8002) sits beside the frame, drawn only when
  the control's "Show Index" is on (the part is not flagged hidden);
- the elements VIEWPORT (``partID`` 28) lives inside the frame; the vertical
  SCROLLBAR track (``partID`` 39) is drawn only when that part is not flagged
  hidden -- a per-control toggle, like the index;
- the control's own caption strip and the index's bezel (``partID`` 30) can
  reserve space above/beside the content: a cluster places a field into its
  VALUE box -- the union of the field's non-label parts, exactly
  ``layout._const_value_box`` -- so every part rect is expressed relative to
  that box's origin, and a top-level array is drawn into that same box.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl, ParsedFPPart
from ...backend import Backend
from ...glyph import Glyph
from ...glyphs.nodes.array_elements import DEFAULT_CELL_H, draw_array_elements
from ...glyphs.nodes.array_scrollbar import draw_array_scrollbar
from ...glyphs.nodes.index_selector import draw_index_control, draw_index_selector
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
from .base import (
    ControlGlyph,
    local_part_rect,
    part_hidden,
    part_rect_of,
    value_box_bounds,
    value_extent,
)

_FRAME_PART_ID = 9
_VIEWPORT_PART_ID = 28
_SCROLLBAR_PART_ID = 39
_INDEX_PART_ID = 8002
_READOUT_PART_ID = 10  # the index's own numLabel
_SPINNER_PART_IDS = (2, 3)  # the index's up / down arrow halves
_INSET = 2.0


@dataclass(frozen=True)
class ArrayControlGlyph(ControlGlyph):
    """One array control. Every ``*_local`` rect is relative to the control's
    VALUE box (origin ``value_origin`` inside the full heap box, size
    ``native_size``); ``draw`` maps them onto whatever box it is handed by one
    uniform scale -- pure translation when the box is the native size."""

    struct_uid: str
    elements: tuple[Glyph, ...]
    default_element: Glyph | None
    cell_h: float
    cell_w: float | None
    value_origin: tuple[float, float]
    native_size: tuple[float, float]
    frame_local: Rect | None
    viewport_local: Rect | None
    scrollbar_local: Rect | None
    index_local: Rect | None
    index_pieces: tuple[Rect, Rect] | None
    show_index: bool

    def value_bounds(self, bounds: Rect) -> Rect:
        return value_box_bounds(bounds, self.value_origin, self.native_size)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        frame = scale_local(self.frame_local, self.native_size, bounds)
        viewport = scale_local(self.viewport_local, self.native_size, bounds)
        if frame is None or viewport is None:
            # A heap without the array's parts: the whole box, inset.
            frame = bounds
            x1, y1, x2, y2 = bounds
            viewport = (x1 + _INSET, y1 + _INSET, x2 - _INSET, y2 - _INSET)
        backend.rect(
            *frame, fill=theme.fp_panel, stroke=theme.struct_border, stroke_width=1.0
        )
        washed: list[Rect] = []
        if self.show_index:
            washed += self._draw_index(backend, bounds, theme)
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
            cell_h=self.cell_h,
            cell_w=self.cell_w,
        )
        scrollbar = scale_local(self.scrollbar_local, self.native_size, bounds)
        if scrollbar is not None:
            draw_array_scrollbar(
                backend, scrollbar, theme, self.struct_uid, len(self.elements), visible
            )
            washed.append(scrollbar)
        if not self.elements:
            # An array with no saved elements is shown disabled: its index and
            # scrollbar carry the same wash as its unset rows.
            for rect in washed:
                backend.begin_group(cls="lv-disabled-mask")
                backend.rect(*rect, fill=theme.disabled_mask)
                backend.end_group()

    def _draw_index(self, backend: Backend, bounds: Rect, theme: Theme) -> list[Rect]:
        """The index control -- spinner + readout from the index part's own
        sub-parts when the heap has them, else the whole index part as one
        box. Returns the rects drawn."""
        if self.index_pieces is not None:
            spinner = scale_local(self.index_pieces[0], self.native_size, bounds)
            readout = scale_local(self.index_pieces[1], self.native_size, bounds)
            if spinner is not None and readout is not None:
                draw_index_control(backend, spinner, readout, theme, self.struct_uid)
                return [spinner, readout]
        box = scale_local(self.index_local, self.native_size, bounds)
        if box is None:
            return []
        draw_index_selector(backend, box, theme, self.struct_uid)
        return [box]


def _index_pieces(
    ctrl: ParsedFPControl, origin: tuple[float, float]
) -> tuple[Rect, Rect] | None:
    """The index display's spinner (its two arrow halves) and readout
    (``numLabel``) rects, from the index part's own sub-parts."""
    index = next((p for p in ctrl.parts if p.part_id == _INDEX_PART_ID), None)
    if index is None:
        return None
    px1, py1, _px2, _py2 = part_rect_of(index)
    ox, oy = origin

    def local(sub: ParsedFPPart) -> Rect:
        top, left, bottom, right = sub.bounds
        return (px1 + left - ox, py1 + top - oy, px1 + right - ox, py1 + bottom - oy)

    halves = [local(p) for p in index.parts if p.part_id in _SPINNER_PART_IDS]
    readout = next(
        (local(p) for p in index.parts if p.part_id == _READOUT_PART_ID), None
    )
    if len(halves) != len(_SPINNER_PART_IDS) or readout is None:
        return None
    spinner = (
        min(r[0] for r in halves),
        min(r[1] for r in halves),
        max(r[2] for r in halves),
        max(r[3] for r in halves),
    )
    return spinner, readout


def array_control(
    ctrl: ParsedFPControl,
    default_element: Glyph | None,
    elements: tuple[Glyph, ...] = (),
) -> ArrayControlGlyph:
    """Build ``ctrl``'s array glyph from its heap parts."""
    ex1, ey1, ex2, ey2 = value_extent(ctrl)
    origin = (ex1, ey1)
    geom = ctrl.cluster_geom
    cell_h = geom.height if geom else None
    if cell_h is None:
        # The element is a scalar/enum: its own real row height, from the
        # element ddo's part (its bounds height divides the viewport into the
        # rows LabVIEW shows).
        element_part = next((p for p in ctrl.parts if p.part_id is None), None)
        if element_part is not None:
            top, _left, bottom, _right = element_part.bounds
            cell_h = bottom - top
    return ArrayControlGlyph(
        struct_uid=ctrl.uid,
        elements=elements,
        default_element=default_element,
        cell_h=float(cell_h) if cell_h is not None else DEFAULT_CELL_H,
        cell_w=geom.width if geom else None,
        value_origin=origin,
        native_size=(ex2 - ex1, ey2 - ey1),
        frame_local=local_part_rect(ctrl, _FRAME_PART_ID, origin),
        viewport_local=local_part_rect(ctrl, _VIEWPORT_PART_ID, origin),
        scrollbar_local=(
            None
            if part_hidden(ctrl, _SCROLLBAR_PART_ID)
            else local_part_rect(ctrl, _SCROLLBAR_PART_ID, origin)
        ),
        index_local=local_part_rect(ctrl, _INDEX_PART_ID, origin),
        index_pieces=_index_pieces(ctrl, origin),
        show_index=not part_hidden(ctrl, _INDEX_PART_ID),
    )
