"""A front-panel numeric control, drawn from its own heap parts.

A numeric control is ONE raised frame (``partID`` 9) holding a spinner at its
left (two arrow halves, ``partID`` 2 / 3) and a narrow readout (``numLabel``
``partID`` 10, bevelled by the ``partID`` 119 cosms) beside it -- not a full-
width value cell with the arrows on the right. The caption strip above is the
cluster's / the top-level label's job, so the drawn box is the control's VALUE
box (``base.value_extent``).

Modern numerics (``style.numeric_style``) are drawn in their own look
(``modern.py``): a field with no outline and an inner shadow, and a spinner of
two pills that is absent when the arrows are hidden (every Modern indicator).
Every other style keeps the generic drawing below.

Without heap parts (an array's disabled default row, a hand-built control) the
glyph is a plain value cell.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl, ParsedFPPart
from ...backend import Backend
from ...glyph import fit_value
from ...glyphs.nodes.index_selector import draw_spinner
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
from . import modern
from .base import (
    FRAME_PART_ID,
    ControlGlyph,
    find_part,
    local_part_rect,
    part_hidden,
    part_is_hidden,
    part_rect_of,
    value_box_bounds,
    value_extent,
)
from .number_format import format_number
from .style import ControlStyle, numeric_style

_SPINNER_PART_IDS = (2, 3)
# A numeric's readout is its ``numLabel``; a ring's or enum's is its item
# ``multiLabel``, its ``numLabel`` being hidden.
_READOUT_PART_IDS = (10, 12)
_READOUT_BEVEL_PART_ID = 119
_TEXT_SIZE = 11.0
_TEXT_PAD = 4.0


@dataclass(frozen=True)
class NumericControlGlyph(ControlGlyph):
    """``value`` is the already-formatted display text. The ``*_local`` rects
    are relative to the value box (origin ``value_origin``, size
    ``native_size``); when they are absent the glyph is a plain value cell."""

    value: str
    show_spinner: bool = True
    value_origin: tuple[float, float] = (0.0, 0.0)
    native_size: tuple[float, float] | None = None
    frame_local: Rect | None = None
    spinner_local: Rect | None = None
    # The two arrow halves that are shown, up first.
    spinner_halves_local: tuple[Rect, ...] = ()
    readout_local: Rect | None = None
    style: ControlStyle = ControlStyle.OTHER
    # The readout part's own literal fill, when it stores one.
    readout_fill: str | None = None
    is_indicator: bool = False

    def value_bounds(self, bounds: Rect) -> Rect:
        if self.native_size is None:
            return bounds
        return value_box_bounds(bounds, self.value_origin, self.native_size)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        frame = scale_local(self.frame_local, self.native_size, bounds)
        readout = scale_local(self.readout_local, self.native_size, bounds)
        if readout is None:
            self._draw_plain(backend, bounds, theme)
        elif self.style == ControlStyle.MODERN:
            self._draw_modern(backend, frame, readout, bounds)
        elif frame is None:
            self._draw_plain(backend, bounds, theme)
        else:
            backend.rect(
                *frame, fill=theme.fp_panel, stroke=theme.struct_border,
                stroke_width=1.0, rx=3.0,
            )
            spinner = scale_local(self.spinner_local, self.native_size, bounds)
            if self.show_spinner and spinner is not None:
                draw_spinner(backend, spinner, theme)
            self._draw_readout(backend, readout, theme)

    def _draw_modern(
        self, backend: Backend, frame: Rect | None, readout: Rect, bounds: Rect
    ) -> None:
        modern.draw_field(
            backend, frame, readout,
            modern.field_fill(self.readout_fill, self.is_indicator),
        )
        if self.show_spinner:
            for up, local in zip((True, False), self.spinner_halves_local):
                half = scale_local(local, self.native_size, bounds)
                if half is not None:
                    modern.draw_pill(backend, half, up)
        text = fit_value(
            self.value or "0",
            readout[2] - readout[0] - 2 * modern.TEXT_INSET,
            backend,
            modern.TEXT_SIZE,
        )
        modern.draw_text(backend, readout, text)

    def _draw_plain(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """A value cell with no spinner: an array's default row."""
        self._draw_readout(backend, bounds, theme)

    def _draw_readout(self, backend: Backend, box: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = box
        backend.rect(
            x1, y1, x2, y2, fill=theme.fp_value_fill,
            stroke=theme.struct_border, stroke_width=1.0,
        )
        text = fit_value(
            self.value or "0", x2 - x1 - 2 * _TEXT_PAD, backend, _TEXT_SIZE
        )
        backend.text(
            x1 + _TEXT_PAD, (y1 + y2) / 2 + 4, text, _TEXT_SIZE,
            fill=theme.fp_value_text, anchor="start",
        )


def numeric_control(
    ctrl: ParsedFPControl, text: str | None = None
) -> NumericControlGlyph:
    """``ctrl``'s numeric glyph, laid out from its heap parts when present.
    ``text`` is the readout text when it is not the control's number (a ring's
    or enum's selected item)."""
    value = text if text is not None else format_number(
        ctrl.default_value or "0", ctrl.number_format
    )
    x1, y1, x2, y2 = value_extent(ctrl)
    origin = (x1, y1)
    frame = local_part_rect(ctrl, FRAME_PART_ID, origin)
    readout_part = _readout_part(ctrl)
    readout = _readout(ctrl, origin)
    if frame is None or readout is None:
        return NumericControlGlyph(value=value)
    halves = tuple(
        rect
        for pid in _SPINNER_PART_IDS
        if not part_hidden(ctrl, pid)
        if (rect := local_part_rect(ctrl, pid, origin)) is not None
    )
    spinner = (
        (
            min(r[0] for r in halves),
            min(r[1] for r in halves),
            max(r[2] for r in halves),
            max(r[3] for r in halves),
        )
        if halves
        else None
    )
    return NumericControlGlyph(
        value=value,
        show_spinner=bool(halves),
        value_origin=origin,
        native_size=(x2 - x1, y2 - y1),
        frame_local=frame,
        spinner_local=spinner,
        spinner_halves_local=halves,
        readout_local=readout,
        style=numeric_style(ctrl),
        readout_fill=readout_part.bg_color if readout_part is not None else None,
        is_indicator=ctrl.shows_as_indicator,
    )


def _readout_part(ctrl: ParsedFPControl) -> ParsedFPPart | None:
    """The part whose text is the readout: the first shown of ``numLabel`` and
    the ring's item ``multiLabel``."""
    for part_id in _READOUT_PART_IDS:
        part = find_part(ctrl, part_id)
        if part is not None and not part_is_hidden(part):
            return part
    return None


def _readout(ctrl: ParsedFPControl, origin: tuple[float, float]) -> Rect | None:
    """The readout box: the bevel cosms around the ``numLabel`` when present,
    else the readout part itself."""
    ox, oy = origin
    bevels = [
        (r[0] - ox, r[1] - oy, r[2] - ox, r[3] - oy)
        for p in ctrl.parts
        if p.part_id == _READOUT_BEVEL_PART_ID
        for r in (part_rect_of(p),)
    ]
    if bevels:
        return (
            min(r[0] for r in bevels),
            min(r[1] for r in bevels),
            max(r[2] for r in bevels),
            max(r[3] for r in bevels),
        )
    part = _readout_part(ctrl)
    if part is None:
        return None
    x1, y1, x2, y2 = part_rect_of(part)
    return (x1 - ox, y1 - oy, x2 - ox, y2 - oy)
