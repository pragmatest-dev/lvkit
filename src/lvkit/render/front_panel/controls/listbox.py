"""A front-panel listbox in the System look: a 1 px frame in the frame part's
stored color, an optional header row, and rows of equal height with a line
between them and a divider between columns.

The rows' text is not drawn: the parser does not keep a listbox's items, so the
grid shows empty. Row check boxes are not drawn either: which listboxes show
them is not decoded from their flags.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl, ParsedFPListbox
from ...backend import Backend
from ...glyph import fit_label
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
from .base import (
    FRAME_PART_ID,
    ControlGlyph,
    find_part,
    local_part_rect,
    value_box_bounds,
    value_extent,
)
from .style import ControlStyle, listbox_style

_GRID_PART_ID = 100

# Rows repeat every 18 px: 17 px of cell and a 1 px line under it.
_ROW_PITCH = 18.0
_LINE = 1.0
_HEADER_FILL = "#C3D3E6"
_DEFAULT_LINE = "#A8ABAE"
_DEFAULT_FILL = "#FFFFFF"
_TEXT = "#000000"
_TEXT_SIZE = 12.0
_TEXT_INSET = 5.0
_NO_BREAK_SPACE = "\u00a0"


@dataclass(frozen=True)
class ListboxControlGlyph(ControlGlyph):
    """``frame_local`` and ``grid_local`` are relative to the value box (origin
    ``value_origin``, size ``native_size``)."""

    listbox: ParsedFPListbox
    value_origin: tuple[float, float]
    native_size: tuple[float, float]
    frame_local: Rect
    grid_local: Rect
    line_color: str | None = None
    fill_color: str | None = None

    def value_bounds(self, bounds: Rect) -> Rect:
        return value_box_bounds(bounds, self.value_origin, self.native_size)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        frame = scale_local(self.frame_local, self.native_size, bounds)
        grid = scale_local(self.grid_local, self.native_size, bounds)
        if frame is None or grid is None:
            return
        line = self.line_color or _DEFAULT_LINE
        backend.rect(
            *frame, fill=self.fill_color or _DEFAULT_FILL, stroke=line,
            stroke_width=1.0,
        )
        gx1, gy1, gx2, gy2 = grid
        if self.listbox.has_header:
            backend.rect(gx1, gy1, gx2, gy1 + _ROW_PITCH - _LINE, fill=_HEADER_FILL)
        y = gy1 + _ROW_PITCH - _LINE
        while y < gy2:
            backend.rect(gx1, y, gx2, y + _LINE, fill=line)
            y += _ROW_PITCH
        x = gx1
        for index, width in enumerate(self.listbox.column_widths):
            if self.listbox.has_header and index < len(self.listbox.column_headers):
                self._draw_header(backend, x, gy1, width, index)
            x += width
            if x - _LINE < gx2:
                backend.rect(x - _LINE, gy1, x, gy2, fill=line)

    def _draw_header(
        self, backend: Backend, x: float, y: float, width: float, index: int
    ) -> None:
        text = self.listbox.column_headers[index]
        stripped = text.lstrip(" ")
        shown = _NO_BREAK_SPACE * (len(text) - len(stripped)) + stripped
        backend.text(
            x + _TEXT_INSET, y + (_ROW_PITCH - _LINE) / 2 + _TEXT_SIZE * 0.36,
            fit_label(shown, width - _TEXT_INSET, backend, _TEXT_SIZE),
            _TEXT_SIZE, fill=_TEXT, anchor="start",
        )


def listbox_control(ctrl: ParsedFPControl) -> ListboxControlGlyph | None:
    """``ctrl``'s System listbox glyph; None for any other listbox."""
    frame = find_part(ctrl, FRAME_PART_ID)
    if (
        ctrl.listbox is None
        or frame is None
        or listbox_style(ctrl) != ControlStyle.SYSTEM
    ):
        return None
    x1, y1, x2, y2 = value_extent(ctrl)
    origin = (x1, y1)
    frame_local = local_part_rect(ctrl, FRAME_PART_ID, origin)
    grid_local = local_part_rect(ctrl, _GRID_PART_ID, origin) or frame_local
    assert frame_local is not None and grid_local is not None
    return ListboxControlGlyph(
        listbox=ctrl.listbox,
        value_origin=origin,
        native_size=(x2 - x1, y2 - y1),
        frame_local=frame_local,
        grid_local=grid_local,
        line_color=frame.fg_color,
        fill_color=frame.bg_color,
    )
