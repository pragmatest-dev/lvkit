"""A front-panel string or path control, drawn in its own look.

The text sits in the field part (``partID`` 11), not the whole box: the caption
strip above it belongs to the label. Modern draws the field with the inner
shadow of ``modern.py``; System a 1 px bordered field; Silver the stored bevel
around it. A path control also has a small browse button at the right (the
nested ``partID`` 8019 Boolean), drawn here as a raised button with a folder
outline of our own.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl
from ...backend import Backend
from ...glyph import fit_label
from ...glyphs.nodes.base import wrap_label
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
from . import modern
from .base import (
    FRAME_PART_ID,
    ControlGlyph,
    find_part,
    local_part_rect,
    part_rect_of,
    value_box_bounds,
    value_extent,
)
from .display_text import path_text, string_text
from .style import STRING_FIELD_PART_ID, ControlStyle, string_style

# The Silver bevel: three ``partID`` 119 cosms, each with its own stored colors.
_BEVEL_PART_ID = 119
# The browse button of a path control.
_BROWSE_PART_ID = 8019

_TEXT_INSET = 3.0
_LINE_GAP = 2.0

_SYSTEM_BORDER = "#ABADB3"
_SYSTEM_CONTROL_FILL = "#FFFFFF"
_SYSTEM_INDICATOR_FILL = "#F0F0F0"
_SYSTEM_TEXT = "#000000"

_SILVER_FRAME_FILL = "#CCCCCC"
_SILVER_FRAME_EDGE = "#646464"

_BUTTON_FACE = "#E1E1E1"
_BUTTON_EDGE = "#ADADAD"
_FOLDER = "#555555"


@dataclass(frozen=True)
class StringControlGlyph(ControlGlyph):
    """``text`` is what the control shows. The ``*_local`` rects are relative
    to the value box (origin ``value_origin``, size ``native_size``)."""

    text: str
    style: ControlStyle
    is_indicator: bool
    value_origin: tuple[float, float]
    native_size: tuple[float, float]
    field_local: Rect
    frame_local: Rect | None = None
    # The field part's own literal fill, when it stores one.
    field_fill: str | None = None
    frame_fill: str | None = None
    frame_edge: str | None = None
    # The Silver bevel cosms in stored order (front first), each ``(rect, fill)``.
    bevel_local: tuple[tuple[Rect, str], ...] = ()
    browse_local: Rect | None = None

    def value_bounds(self, bounds: Rect) -> Rect:
        return value_box_bounds(bounds, self.value_origin, self.native_size)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        field = scale_local(self.field_local, self.native_size, bounds)
        if field is None:
            return
        frame = scale_local(self.frame_local, self.native_size, bounds)
        if self.style == ControlStyle.MODERN:
            modern.draw_field(
                backend, frame, field,
                modern.field_fill(self.field_fill, self.is_indicator),
            )
            text_fill = modern.TEXT
        elif self.style == ControlStyle.SYSTEM:
            fill = (
                _SYSTEM_INDICATOR_FILL if self.is_indicator else _SYSTEM_CONTROL_FILL
            )
            backend.rect(
                *field, fill=fill, stroke=_SYSTEM_BORDER, stroke_width=1.0
            )
            text_fill = _SYSTEM_TEXT
        else:
            self._draw_silver(backend, frame, field, bounds)
            text_fill = modern.TEXT
        self._draw_text(backend, field, text_fill)
        browse = scale_local(self.browse_local, self.native_size, bounds)
        if browse is not None:
            _draw_browse_button(backend, browse)

    def _draw_silver(
        self, backend: Backend, frame: Rect | None, field: Rect, bounds: Rect
    ) -> None:
        if frame is not None:
            backend.rect(
                *frame,
                fill=self.frame_fill or _SILVER_FRAME_FILL,
                stroke=self.frame_edge or _SILVER_FRAME_EDGE,
                stroke_width=1.0,
            )
        for local, fill in reversed(self.bevel_local):
            rect = scale_local(local, self.native_size, bounds)
            if rect is not None:
                backend.rect(*rect, fill=fill)
        backend.rect(*field, fill=modern.field_fill(self.field_fill, self.is_indicator))

    def _draw_text(self, backend: Backend, field: Rect, fill: str) -> None:
        x1, y1, x2, y2 = field
        size = modern.TEXT_SIZE
        width = x2 - x1 - 2 * _TEXT_INSET
        line_h = size + _LINE_GAP
        max_lines = max(1, int((y2 - y1) / line_h))
        lines: list[str] = []
        for segment in self.text.split("\n"):
            if not segment:
                lines.append("")
            else:
                lines.extend(wrap_label(segment, width, backend, size, max_lines))
        if max_lines == 1:
            text = fit_label(" ".join(lines), width, backend, size)
            backend.text(
                x1 + _TEXT_INSET, (y1 + y2) / 2 + size * 0.36, text, size,
                fill=fill, anchor="start",
            )
            return
        for i, line in enumerate(lines[:max_lines]):
            backend.text(
                x1 + _TEXT_INSET, y1 + size + i * line_h, line, size,
                fill=fill, anchor="start",
            )


def _draw_browse_button(backend: Backend, rect: Rect) -> None:
    x1, y1, x2, y2 = rect
    backend.rect(x1, y1, x2, y2, fill=_BUTTON_FACE, stroke=_BUTTON_EDGE,
                 stroke_width=1.0, rx=2.0)
    side = min(x2 - x1, y2 - y1) * 0.5
    fx = (x1 + x2) / 2 - side / 2
    fy = (y1 + y2) / 2 - side * 0.4
    tab = side * 0.3
    backend.polygon(
        [
            (fx, fy + tab), (fx + side * 0.45, fy + tab),
            (fx + side * 0.55, fy), (fx + side, fy),
            (fx + side, fy + side * 0.8), (fx, fy + side * 0.8),
        ],
        fill="none", stroke=_FOLDER, stroke_width=1.0,
    )


def string_control(ctrl: ParsedFPControl) -> StringControlGlyph | None:
    """``ctrl``'s string or path glyph, laid out from its field part; None when
    it has none, or its look is not one drawn here."""
    style = string_style(ctrl)
    field_part = find_part(ctrl, STRING_FIELD_PART_ID)
    if style == ControlStyle.OTHER or field_part is None:
        return None
    x1, y1, x2, y2 = value_extent(ctrl)
    origin = (x1, y1)
    field = local_part_rect(ctrl, STRING_FIELD_PART_ID, origin)
    assert field is not None
    frame_part = find_part(ctrl, FRAME_PART_ID)
    browse = find_part(ctrl, _BROWSE_PART_ID)
    is_path = ctrl.control_type == "stdPath"
    return StringControlGlyph(
        text=path_text(ctrl.default_value)
        if is_path
        else string_text(ctrl.default_value),
        style=style,
        is_indicator=ctrl.shows_as_indicator,
        value_origin=origin,
        native_size=(x2 - x1, y2 - y1),
        field_local=field,
        frame_local=local_part_rect(ctrl, FRAME_PART_ID, origin),
        field_fill=field_part.bg_color,
        frame_fill=frame_part.bg_color if frame_part is not None else None,
        frame_edge=frame_part.fg_color if frame_part is not None else None,
        bevel_local=_bevel(ctrl, origin) if style == ControlStyle.SILVER else (),
        browse_local=(
            local_part_rect(ctrl, _BROWSE_PART_ID, origin)
            if is_path and browse is not None and not browse.hidden
            else None
        ),
    )


def _bevel(
    ctrl: ParsedFPControl, origin: tuple[float, float]
) -> tuple[tuple[Rect, str], ...]:
    ox, oy = origin
    result = []
    for part in ctrl.parts:
        if part.part_id != _BEVEL_PART_ID or part.bg_color is None:
            continue
        x1, y1, x2, y2 = part_rect_of(part)
        result.append(((x1 - ox, y1 - oy, x2 - ox, y2 - oy), part.bg_color))
    return tuple(result)
