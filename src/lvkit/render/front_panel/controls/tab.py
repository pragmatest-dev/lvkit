"""A tab control: a row of tabs with the page names, the shown one joined to the
frame of the page. Plain rects and text; the shown page's objects are placed by
``geometry``.

The tab control's own parts give the geometry: part 28 is the page frame and
the ``tabArray`` part (8018) holds the row band (sub-part 93), which can reach
outside the control's bounds (above them in a control whose bounds are the
frame). The tab names' widths are not read, so the tabs share the band equally.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl
from ...backend import Backend
from ...glyph import fit_label
from ...style import Theme
from .base import ControlGlyph, find_part, part_rect_of

_FRAME_PART_ID = 28
_TAB_ARRAY_PART_ID = 8018
_TAB_ROW_PART_ID = 93
_SIZE = 10.0


@dataclass(frozen=True)
class TabControlGlyph(ControlGlyph):
    """``names`` are the page names in tab order and ``shown`` the index of the
    shown page. ``frame`` is the page frame and ``row`` the tab row band, both in
    the control's own space; ``frame`` None is the whole control box and ``row``
    None draws no tabs."""

    names: tuple[str, ...]
    shown: int
    frame: Rect | None
    row: Rect | None

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        frame = _moved(self.frame, x1, y1) if self.frame else bounds
        backend.rect(
            *frame, fill=theme.fp_panel, stroke=theme.struct_border, stroke_width=1.0
        )
        if self.row is None or not self.names:
            return
        band = _moved(self.row, x1, y1)
        side = _open_side(band, frame)
        for index, tab in enumerate(_tabs(band, frame, len(self.names), side)):
            if index == self.shown:
                _draw_shown_tab(backend, tab, band, side, theme)
            else:
                backend.rect(
                    *tab,
                    fill=theme.fp_index_fill,
                    stroke=theme.struct_border,
                    stroke_width=1.0,
                )
            text = fit_label(self.names[index], tab[2] - tab[0] - 6, backend, _SIZE)
            backend.text(
                (tab[0] + tab[2]) / 2,
                (tab[1] + tab[3]) / 2 + _SIZE * 0.34,
                text,
                _SIZE,
                fill=theme.text,
            )


def _moved(rect: Rect, dx: float, dy: float) -> Rect:
    return (rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy)


def _open_side(band: Rect, frame: Rect) -> str:
    """The side of a tab that faces the page frame: ``bottom`` for a row above
    the frame, ``top`` below it, ``right`` for a column left of it, ``left``
    right of it."""
    bx1, by1, bx2, by2 = band
    fx1, fy1, fx2, fy2 = frame
    if bx2 - bx1 >= by2 - by1:
        return "bottom" if (by1 + by2) < (fy1 + fy2) else "top"
    return "right" if (bx1 + bx2) < (fx1 + fx2) else "left"


def _tabs(band: Rect, frame: Rect, count: int, side: str) -> list[Rect]:
    """The tabs of ``count`` pages: equal shares of ``band`` along its long side,
    each stopping at the page frame."""
    bx1, by1, bx2, by2 = band
    fx1, fy1, fx2, fy2 = frame
    if side in ("bottom", "top"):
        top, bottom = (by1, min(by2, fy1)) if side == "bottom" else (max(by1, fy2), by2)
        step = (bx2 - bx1) / count
        return [
            (bx1 + i * step, top, bx1 + (i + 1) * step, bottom) for i in range(count)
        ]
    left, right = (bx1, min(bx2, fx1)) if side == "right" else (max(bx1, fx2), bx2)
    step = (by2 - by1) / count
    return [(left, by1 + i * step, right, by1 + (i + 1) * step) for i in range(count)]


def _draw_shown_tab(
    backend: Backend, tab: Rect, band: Rect, side: str, theme: Theme
) -> None:
    """The shown tab in the page's colour, reaching across the frame's edge as
    far as the band does and open on the side facing the page, so tab and page
    read as one piece."""
    x1, y1, x2, y2 = tab
    if side == "bottom":
        y2 = band[3]
        outline = [(x1, y2), (x1, y1), (x2, y1), (x2, y2)]
    elif side == "top":
        y1 = band[1]
        outline = [(x1, y1), (x1, y2), (x2, y2), (x2, y1)]
    elif side == "right":
        x2 = band[2]
        outline = [(x2, y1), (x1, y1), (x1, y2), (x2, y2)]
    else:
        x1 = band[0]
        outline = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    backend.rect(x1, y1, x2, y2, fill=theme.fp_panel)
    backend.path(outline, stroke=theme.struct_border, stroke_width=1.0)


def tab_row_rect(ctrl: ParsedFPControl) -> Rect | None:
    """The tab row band of ``ctrl`` in the control's own space, None when the
    control records none."""
    array = find_part(ctrl, _TAB_ARRAY_PART_ID)
    if array is None:
        return None
    row = next((p for p in array.parts if p.part_id == _TAB_ROW_PART_ID), None)
    if row is None:
        return None
    ox, oy = part_rect_of(array)[:2]
    return _moved(part_rect_of(row), ox, oy)


def tab_control(ctrl: ParsedFPControl) -> TabControlGlyph:
    """The glyph of ``ctrl`` (a ``tabControl``): its page names, shown page,
    page frame and tab row."""
    shown = ctrl.tab_selected if ctrl.tab_selected is not None else 0
    frame = find_part(ctrl, _FRAME_PART_ID)
    return TabControlGlyph(
        tuple(p.name for p in ctrl.tab_pages),
        shown,
        part_rect_of(frame) if frame is not None else None,
        tab_row_rect(ctrl),
    )
