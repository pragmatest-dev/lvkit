"""The value glyph for an ``absTime`` (Timestamp) control, drawn from its own
heap parts.

Real corpus heap (issue #101's repro "Time Stamp" control): the SAME frame
(``partID`` 9) / spinner halves (``partID`` 2, 3) / bevelled ``numLabel``
readout (``partID`` 10, bevel 119) a numeric control uses -- see
``numeric.py`` -- plus one addition, a nested ``stdBool`` at ``partID`` 8019:
LabVIEW's calendar/time picker toggle button.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl
from ...backend import Backend
from ...glyph import fit_label
from ...glyphs.nodes.index_selector import draw_spinner
from ...glyphs.nodes.local_rect import scale_local
from ...nodes import _format_timestamp_lines
from ...style import Theme
from .base import (
    FRAME_PART_ID,
    ControlGlyph,
    local_part_rect,
    part_rect_of,
    value_box_bounds,
    value_extent,
)

_SPINNER_PART_IDS = (2, 3)
_READOUT_PART_ID = 10
_READOUT_BEVEL_PART_ID = 119
_BUTTON_PART_ID = 8019
_TEXT_SIZE = 9.0
# LabVIEW's real "never assigned" display for a Timestamp control -- NOT a
# decoded value (confirmed: no bytes anywhere in the heap spell this text;
# the only recorded field is the format spec ``"%<%.3X\n%x>T"``, a generic
# locale template). Confirmed directly by the maintainer as LabVIEW's own
# fixed convention for this state, distinct from a real saved value that
# happens to decode to the epoch (which shows the real decoded date/time,
# e.g. via ``_format_timestamp_lines``) -- a control with no ``DefaultData``
# at all is "never set", not "set to 1/1/1904".
_UNASSIGNED_TEXT = "00:00:00.000 PM\nMM/DD/YYYY"


@dataclass(frozen=True)
class TimestampControlGlyph(ControlGlyph):
    """``value`` is the already-formatted two-line (time, then date) display
    text (see ``_format_timestamp_lines``), joined by ``\\n``. The ``*_local``
    rects are relative to the value box (origin ``value_origin``, size
    ``native_size``); when they are absent the glyph is a plain two-line
    value cell with no spinner or button (parts-less fallback)."""

    value: str
    value_origin: tuple[float, float] = (0.0, 0.0)
    native_size: tuple[float, float] | None = None
    frame_local: Rect | None = None
    spinner_local: Rect | None = None
    readout_local: Rect | None = None
    button_local: Rect | None = None

    def value_bounds(self, bounds: Rect) -> Rect:
        if self.native_size is None:
            return bounds
        return value_box_bounds(bounds, self.value_origin, self.native_size)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        frame = scale_local(self.frame_local, self.native_size, bounds)
        readout = scale_local(self.readout_local, self.native_size, bounds)
        if frame is None or readout is None:
            self._draw_readout(backend, bounds, theme)
            return
        backend.rect(
            *frame, fill=theme.fp_panel, stroke=theme.struct_border,
            stroke_width=1.0, rx=3.0,
        )
        spinner = scale_local(self.spinner_local, self.native_size, bounds)
        if spinner is not None:
            draw_spinner(backend, spinner, theme)
        self._draw_readout(backend, readout, theme)
        button = scale_local(self.button_local, self.native_size, bounds)
        if button is not None:
            self._draw_calendar_button(backend, button, theme)

    def _draw_readout(self, backend: Backend, box: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = box
        backend.rect(
            x1, y1, x2, y2, fill=theme.fp_value_fill,
            stroke=theme.struct_border, stroke_width=1.0,
        )
        lines = self.value.split("\n")
        n = len(lines)
        for i, line in enumerate(lines):
            text = fit_label(line, x2 - x1 - 4, backend, _TEXT_SIZE)
            ty = y1 + (y2 - y1) * (i + 1) / (n + 1) + _TEXT_SIZE * 0.3
            backend.text(
                (x1 + x2) / 2, ty, text, _TEXT_SIZE, fill=theme.fp_value_text,
            )

    def _draw_calendar_button(self, backend: Backend, box: Rect, theme: Theme) -> None:
        """A generic calendar icon (frame + header band + two binder tabs) --
        an unverified generic convention, not checked against any specific
        vendor's icon (same caveat as ``PictureControlGlyph``'s placeholder)."""
        x1, y1, x2, y2 = box
        backend.rect(
            x1, y1, x2, y2, fill=theme.fp_panel,
            stroke=theme.struct_border, stroke_width=1.0, rx=2.0,
        )
        w, h = x2 - x1, y2 - y1
        header_h = h * 0.32
        backend.rect(
            x1, y1, x2, y1 + header_h,
            fill=theme.pane_type_text, stroke="none",
        )
        tab_w = max(w * 0.08, 1.0)
        tab_h = header_h * 0.6
        for frac in (0.28, 0.72):
            tx = x1 + w * frac
            backend.line(
                tx, y1, tx, y1 + tab_h,
                stroke=theme.fp_panel, stroke_width=tab_w,
            )


def timestamp_control(ctrl: ParsedFPControl) -> TimestampControlGlyph:
    """``ctrl``'s Timestamp glyph, laid out from its heap parts when present.

    ``ctrl.default_value is None`` means no ``DefaultData`` was saved at all
    (genuinely never assigned) -- shown as LabVIEW's own fixed placeholder
    (``_UNASSIGNED_TEXT``), never run through ``_format_timestamp_lines``
    (which decodes a REAL saved value, including one that happens to equal
    the epoch -- a different state, shown differently in real LabVIEW)."""
    value = (
        _UNASSIGNED_TEXT
        if ctrl.default_value is None
        else _format_timestamp_lines(ctrl.default_value)
    )
    x1, y1, x2, y2 = value_extent(ctrl)
    origin = (x1, y1)
    frame = local_part_rect(ctrl, FRAME_PART_ID, origin)
    readout = _readout(ctrl, origin)
    button = local_part_rect(ctrl, _BUTTON_PART_ID, origin)
    halves = [
        r
        for pid in _SPINNER_PART_IDS
        if (r := local_part_rect(ctrl, pid, origin)) is not None
    ]
    if frame is None or readout is None or not halves:
        return TimestampControlGlyph(value=value)
    spinner = (
        min(r[0] for r in halves),
        min(r[1] for r in halves),
        max(r[2] for r in halves),
        max(r[3] for r in halves),
    )
    return TimestampControlGlyph(
        value=value,
        value_origin=origin,
        native_size=(x2 - x1, y2 - y1),
        frame_local=frame,
        spinner_local=spinner,
        readout_local=readout,
        button_local=button,
    )


def _readout(ctrl: ParsedFPControl, origin: tuple[float, float]) -> Rect | None:
    """The readout box: the bevel cosms around the ``numLabel`` when present,
    else the ``numLabel`` itself -- same convention as ``numeric._readout``."""
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
    return local_part_rect(ctrl, _READOUT_PART_ID, origin)
