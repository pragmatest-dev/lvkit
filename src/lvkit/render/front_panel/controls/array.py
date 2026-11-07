"""The front-panel array control and its own drawing rules.

A front-panel array is drawn from the heap's own parts, not a synthetic
layout:

- the FRAME (``partID`` 9) surrounds the elements and scrollbar only -- the
  index selector is NOT inside it;
- the INDEX selectors (``partID`` 8002) sit beside the frame, one per array
  dimension (an N-D array carries N of them, stacked), each drawn only when
  its part is not flagged hidden -- the control's "Show Index". Each is drawn
  inside its own frame (the index part's ``partID`` 9 sub-part); the bezel
  part (``partID`` 30) is larger than the index and is not drawn;
- each index display is a numeric of its own: its sub-parts are drawn by the
  numeric drawing (``numeric.numeric_from_parts``), as is a numeric element,
  so the style tables apply to them as to any numeric;
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

from ....parser.flags import is_indicator
from ....parser.layout import Rect
from ....parser.models import ParsedFPControl, ParsedFPPart
from ...backend import Backend
from ...glyph import Glyph
from ...glyphs.nodes.array_elements import DEFAULT_CELL_H, draw_array_elements
from ...glyphs.nodes.array_scrollbar import draw_array_scrollbar
from ...glyphs.nodes.index_selector import draw_index_control, draw_index_selector
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
from . import modern
from .base import (
    FRAME_PART_ID,
    ControlGlyph,
    frame_colors,
    local_part_rect,
    part_hidden,
    part_rect_of,
    value_bounds,
    value_box_bounds,
    value_extent,
)
from .numeric import NumericControlGlyph, numeric_from_parts
from .style import ControlStyle

_VIEWPORT_PART_ID = 28
_SCROLLBAR_PART_ID = 39
_INDEX_PART_ID = 8002
_INDEX_FRAME_RADIUS = 3.0
_READOUT_PART_ID = 10  # the index's own numLabel
_SPINNER_PART_IDS = (2, 3)  # the index's up / down arrow halves
_INSET = 2.0


@dataclass(frozen=True)
class IndexDisplay:
    """One dimension's index: its part ``box`` and, when the heap has them, its
    ``frame`` and the ``pieces`` (spinner, readout) inside it. Rects are
    value-box-local."""

    box: Rect
    frame: Rect | None
    pieces: tuple[Rect, Rect] | None
    # The index drawn as the numeric its sub-parts make; None when they do not
    # make one (``frame`` and ``pieces`` are then drawn).
    numeric: NumericControlGlyph | None = None


@dataclass(frozen=True)
class ElementRowGlyph:
    """An element drawn by its own control (``glyph``): ``draw`` takes the
    element's row and puts the control's value box where its parts say."""

    glyph: NumericControlGlyph

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        self.glyph.draw(backend, value_bounds(self.glyph, bounds), theme)


def _draws_disabled(glyph: Glyph | None) -> bool:
    return isinstance(glyph, ElementRowGlyph) and glyph.glyph.draws_disabled


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
    frame_fill: str | None
    frame_stroke: str | None
    viewport_local: Rect | None
    scrollbar_local: Rect | None
    indices: tuple[IndexDisplay, ...]

    def value_bounds(self, bounds: Rect) -> Rect:
        return value_box_bounds(bounds, self.value_origin, self.native_size)

    @property
    def _elements_are_controls(self) -> bool:
        """True when the elements are numerics drawn from their own parts: each
        fills its whole row, since its parts are placed from the row's top-left."""
        glyph = self.default_element
        return (
            isinstance(glyph, ElementRowGlyph)
            and glyph.glyph.native_size is not None
        )

    @property
    def _modern_elements(self) -> bool:
        """True when the elements are Modern numerics: the frame is then drawn in
        the Modern look, with no outline."""
        glyph = self.default_element
        return (
            isinstance(glyph, ElementRowGlyph)
            and glyph.glyph.style == ControlStyle.MODERN
        )

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        frame = scale_local(self.frame_local, self.native_size, bounds)
        viewport = scale_local(self.viewport_local, self.native_size, bounds)
        if frame is None or viewport is None:
            # A heap without the array's parts: the whole box, inset.
            frame = bounds
            x1, y1, x2, y2 = bounds
            viewport = (x1 + _INSET, y1 + _INSET, x2 - _INSET, y2 - _INSET)
        if self._modern_elements:
            modern.draw_frame(
                backend, frame, viewport, self.frame_fill or theme.fp_panel
            )
        else:
            backend.rect(
                *frame,
                fill=self.frame_fill or theme.fp_panel,
                stroke=self.frame_stroke or theme.struct_border,
                stroke_width=1.0,
            )
        washed = self._draw_indices(backend, bounds, theme)
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
            cell_inset=0.0 if self._elements_are_controls else 1.0,
            wash_default=not _draws_disabled(self.default_element),
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

    def _draw_indices(
        self, backend: Backend, bounds: Rect, theme: Theme
    ) -> list[Rect]:
        """Every shown dimension's index, each inside its own frame. Only the
        first dimension is the array controller's live target. Returns the rects
        drawn (each index's spinner and readout)."""
        drawn: list[Rect] = []
        for dimension, index in enumerate(self.indices):
            uid = self.struct_uid if dimension == 0 else None
            box_local = index.box
            box = scale_local(box_local, self.native_size, bounds)
            if box is None:
                continue
            if index.numeric is not None and index.numeric.readout_local is not None:
                ox, oy = index.numeric.value_origin
                nw, nh = index.numeric.native_size or (0.0, 0.0)
                numeric_box = scale_local(
                    (box_local[0] + ox, box_local[1] + oy,
                     box_local[0] + ox + nw, box_local[1] + oy + nh),
                    self.native_size, bounds,
                )
                if numeric_box is not None:
                    index.numeric.draw(backend, numeric_box, theme)
                    if not index.numeric.draws_disabled:
                        drawn.append(numeric_box)
                    continue
            frame = scale_local(index.frame, self.native_size, bounds)
            if frame is not None:
                backend.rect(
                    *frame, fill=theme.fp_panel, stroke=theme.struct_border,
                    stroke_width=1.0, rx=_INDEX_FRAME_RADIUS,
                )
            if index.pieces is not None:
                spinner = scale_local(index.pieces[0], self.native_size, bounds)
                readout = scale_local(index.pieces[1], self.native_size, bounds)
                if spinner is not None and readout is not None:
                    draw_index_control(backend, spinner, readout, theme, uid)
                    drawn += [spinner, readout]
                    continue
            draw_index_selector(backend, box, theme, uid)
            drawn.append(box)
        return drawn


def _index_frame(index: ParsedFPPart, origin: tuple[float, float]) -> Rect | None:
    """The index display's own frame: its ``partID`` 9 sub-part."""
    frame = next((p for p in index.parts if p.part_id == FRAME_PART_ID), None)
    if frame is None:
        return None
    px1, py1, _px2, _py2 = part_rect_of(index)
    top, left, bottom, right = frame.bounds
    ox, oy = origin
    return (px1 + left - ox, py1 + top - oy, px1 + right - ox, py1 + bottom - oy)


def _index_pieces(
    index: ParsedFPPart, origin: tuple[float, float]
) -> tuple[Rect, Rect] | None:
    """The index display's spinner (its two arrow halves) and readout
    (``numLabel``) rects, from the index part's own sub-parts."""
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
    frame_fill, frame_stroke = frame_colors(ctrl)
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
        frame_local=local_part_rect(ctrl, FRAME_PART_ID, origin),
        frame_fill=frame_fill,
        frame_stroke=frame_stroke,
        viewport_local=local_part_rect(ctrl, _VIEWPORT_PART_ID, origin),
        scrollbar_local=(
            None
            if part_hidden(ctrl, _SCROLLBAR_PART_ID)
            else local_part_rect(ctrl, _SCROLLBAR_PART_ID, origin)
        ),
        indices=_indices(ctrl, origin, disabled=not elements),
    )


def _indices(
    ctrl: ParsedFPControl, origin: tuple[float, float], disabled: bool
) -> tuple[IndexDisplay, ...]:
    """One display per shown ``partID`` 8002 part, in heap order from the
    top-most (dimension 0) down. ``disabled`` marks an array with no saved
    elements; a Modern index looks the same then, other looks are washed."""
    ox, oy = origin
    shown = sorted(
        (
            p
            for p in ctrl.parts
            if p.part_id == _INDEX_PART_ID and not p.hidden
        ),
        key=lambda p: (part_rect_of(p)[1], part_rect_of(p)[0]),
    )
    result = []
    for dimension, part in enumerate(shown):
        x1, y1, x2, y2 = part_rect_of(part)
        box = (x1 - ox, y1 - oy, x2 - ox, y2 - oy)
        result.append(
            IndexDisplay(
                box,
                _index_frame(part, origin),
                _index_pieces(part, origin),
                numeric_from_parts(
                    part,
                    "0",
                    is_indicator(part.flags),
                    disabled,
                    live_struct=ctrl.uid if dimension == 0 else None,
                    is_index=True,
                ),
            )
        )
    return tuple(result)
