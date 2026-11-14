"""A front-panel numeric control, drawn from its own heap parts.

A numeric control is ONE raised frame (``partID`` 9) holding a spinner at its
left (two arrow halves, ``partID`` 2 / 3) and a narrow readout (``numLabel``
``partID`` 10, bevelled by the ``partID`` 119 cosms) beside it -- not a full-
width value cell with the arrows on the right. The caption strip above is the
cluster's / the top-level label's job, so the drawn box is the control's VALUE
box (``base.value_extent``).

Modern numerics (``style.numeric_style``) are drawn in their own look
(``modern.py``): a field with no outline and an inner shadow, and a spinner that
is one capsule, absent when the arrows are hidden (every Modern indicator).
Every other style keeps the generic drawing below.

Without heap parts (an array's disabled default row, a hand-built control) the
glyph is a plain value cell.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl, ParsedFPFont, ParsedFPPart
from ...backend import Backend
from ...glyph import fit_value
from ...glyphs.nodes.index_selector import draw_spinner
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
from ...text_style import draw_styled_text, text_style
from . import modern
from .base import (
    FRAME_PART_ID,
    ControlGlyph,
    PartOwner,
    find_part,
    local_part_rect,
    part_hidden,
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
    # In an array with no saved elements. An element is drawn greyed; an index
    # display is not (it only keeps the wash off, see ``draws_disabled``).
    disabled: bool = False
    # The glyph is an array's index display.
    is_index: bool = False
    # The glyph is an array's element: its shadow lies over the array frame's.
    in_array: bool = False
    # The array this is the index display of, when it is the array controller's
    # live target: its arrows page the array and its readout shows the index.
    live_struct: str | None = None
    # The readout part's first font run and its literal colour.
    value_font: ParsedFPFont | None = None
    value_color: str | None = None

    @property
    def greyed(self) -> bool:
        """True when the Modern field is drawn in the disabled look."""
        return self.disabled and not self.is_index

    @property
    def draws_disabled(self) -> bool:
        """True when ``disabled`` is drawn by the glyph itself (the Modern look),
        so the caller must not wash it again."""
        return self.disabled and self.style == ControlStyle.MODERN

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
                *frame,
                fill=theme.fp_panel,
                stroke=theme.struct_border,
                stroke_width=1.0,
                rx=3.0,
            )
            spinner = scale_local(self.spinner_local, self.native_size, bounds)
            if self.show_spinner and spinner is not None:
                draw_spinner(backend, spinner, theme, self.live_struct)
            self._draw_readout(backend, readout, theme)

    def _draw_modern(
        self, backend: Backend, frame: Rect | None, readout: Rect, bounds: Rect
    ) -> None:
        modern.draw_field(
            backend,
            frame,
            readout,
            modern.field_fill(self.readout_fill, self.is_indicator, self.greyed),
            (
                modern.ARRAY_ELEMENT_SHADOW_STOPS
                if self.in_array
                else modern.SHADOW_STOPS
            ),
        )
        if self.show_spinner:
            halves = tuple(
                half
                for local in self.spinner_halves_local
                if (half := scale_local(local, self.native_size, bounds)) is not None
            )
            modern.draw_spinner(backend, halves, self.greyed)
            for up, half in zip((True, False), halves):
                self._begin_arrow(backend, half, up)
                self._end_group(backend)
        style = text_style(
            self.value_font,
            modern.DISABLED_TEXT if self.greyed else self.value_color,
            modern.TEXT_SIZE,
        )
        text = fit_value(
            self.value or "0",
            readout[2] - readout[0] - 2 * modern.TEXT_INSET,
            backend,
            style.size,
        )
        self._begin_index_text(backend)
        modern.draw_text(backend, readout, text, style=style)
        self._end_group(backend)

    def _begin_arrow(self, backend: Backend, half: Rect, up: bool) -> None:
        """Open the click target of one arrow when the glyph is live."""
        if self.live_struct is None:
            return
        backend.begin_group(
            cls="lv-selector lv-clickable",
            data={"lv-action": "next" if up else "prev", "lv-struct": self.live_struct},
        )
        backend.rect(*half, fill="transparent", stroke="none")

    def _begin_index_text(self, backend: Backend) -> None:
        if self.live_struct is not None:
            backend.begin_group(
                cls="lv-array-index", data={"lv-struct": self.live_struct}
            )

    def _end_group(self, backend: Backend) -> None:
        if self.live_struct is not None:
            backend.end_group()

    def _draw_plain(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """A value cell with no spinner: an array's default row."""
        self._draw_readout(backend, bounds, theme)

    def _draw_readout(self, backend: Backend, box: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = box
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=theme.fp_value_fill,
            stroke=theme.struct_border,
            stroke_width=1.0,
        )
        style = text_style(self.value_font, self.value_color, _TEXT_SIZE)
        text = fit_value(
            self.value or "0", x2 - x1 - 2 * _TEXT_PAD, backend, style.size
        )
        self._begin_index_text(backend)
        draw_styled_text(
            backend,
            x1 + _TEXT_PAD,
            (y1 + y2) / 2 + style.size * (4 / _TEXT_SIZE),
            text,
            style,
            theme.fp_value_text,
        )
        self._end_group(backend)


def numeric_control(
    ctrl: ParsedFPControl, text: str | None = None
) -> NumericControlGlyph:
    """``ctrl``'s numeric glyph, laid out from its heap parts when present.
    ``text`` is the readout text when it is not the control's number (a ring's
    or enum's selected item)."""
    value = (
        text
        if text is not None
        else format_number(ctrl.default_value or "0", ctrl.number_format)
    )
    return numeric_from_parts(ctrl, value, ctrl.shows_as_indicator)


def numeric_from_parts(
    owner: PartOwner,
    value: str,
    is_indicator: bool,
    disabled: bool = False,
    live_struct: str | None = None,
    is_index: bool = False,
    in_array: bool = False,
) -> NumericControlGlyph:
    """The numeric glyph of whatever holds the parts: a control, or a part with
    sub-parts of its own (an array's index display or element). ``value`` is the
    display text. ``live_struct`` names the array it is the live index of;
    ``is_index`` marks an array's index display, ``in_array`` an array's
    element."""
    x1, y1, x2, y2 = value_extent(owner)
    origin = (x1, y1)
    frame = local_part_rect(owner, FRAME_PART_ID, origin)
    readout_part = _readout_part(owner)
    readout = _readout(owner, origin)
    if frame is None or readout is None:
        return NumericControlGlyph(
            value=value,
            disabled=disabled,
            live_struct=live_struct,
            is_index=is_index,
            in_array=in_array,
        )
    halves = tuple(
        rect
        for pid in _SPINNER_PART_IDS
        if not part_hidden(owner, pid)
        if (rect := local_part_rect(owner, pid, origin)) is not None
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
        style=numeric_style(owner),
        readout_fill=readout_part.bg_color if readout_part is not None else None,
        is_indicator=is_indicator,
        disabled=disabled,
        is_index=is_index,
        in_array=in_array,
        live_struct=live_struct,
        value_font=readout_part.font if readout_part is not None else None,
        value_color=readout_part.text_color if readout_part is not None else None,
    )


def _readout_part(ctrl: PartOwner) -> ParsedFPPart | None:
    """The part whose text is the readout: the first shown of ``numLabel`` and
    the ring's item ``multiLabel``."""
    for part_id in _READOUT_PART_IDS:
        part = find_part(ctrl, part_id)
        if part is not None and not part.hidden:
            return part
    return None


def _readout(ctrl: PartOwner, origin: tuple[float, float]) -> Rect | None:
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
