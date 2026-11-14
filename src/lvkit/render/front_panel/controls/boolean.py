"""A front-panel boolean control, drawn from its own heap parts.

The heap's value box is the union of the boolean's parts -- the button face,
its shadow and the divot around it -- so the control is drawn in the body part's
rect (``partID`` 21), not the whole box. Each kind of Boolean (``style.
BooleanKind``) has its own drawing (``boolean_kinds.py``); a Boolean whose body
is none of them, and one with no heap parts, is a slide-switch track and thumb
that fills the box it is given.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl, ParsedFPPart
from ...backend import Backend
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
from ...text_style import TextStyle, part_style
from . import boolean_kinds as kinds
from .base import (
    ControlGlyph,
    find_part,
    local_part_rect,
    value_box_bounds,
    value_extent,
)
from .style import BOOLEAN_BODY_PART_ID, BooleanKind, boolean_kind

_BEZEL_PART_ID = 87
_THUMB_PART_ID = 86
_CAPTION_PART_ID = 22


@dataclass(frozen=True)
class PartPicture:
    """A picture embedded in the VI, drawn into the rect of the part that names
    it: ``href`` is its ``data:`` URL, ``rect`` is value-box-local."""

    rect: Rect
    href: str
    is_body: bool


@dataclass(frozen=True)
class BooleanControlGlyph(ControlGlyph):
    """A front-panel boolean control. A control of no measured kind is a
    slide-switch track + thumb, clean-room original chrome (never a copy of
    LabVIEW's own switch art) -- a different, correct visual metaphor from
    ``BooleanConstantGlyph``'s fixed push-button T/F box, which a CONSTANT
    (never resized, never "on/off" as a control is) always uses regardless of
    style.

    The ``*_local`` rects are relative to the value box (origin
    ``value_origin``, size ``native_size``). ``state_color`` and
    ``thumb_color`` are the stored colors of the current state."""

    on: bool
    value_origin: tuple[float, float] = (0.0, 0.0)
    native_size: tuple[float, float] | None = None
    button_local: Rect | None = None
    kind: BooleanKind = BooleanKind.OTHER
    bezel_local: Rect | None = None
    thumb_local: Rect | None = None
    state_color: str | None = None
    thumb_color: str | None = None
    caption: str = ""
    caption_style: TextStyle = TextStyle(kinds.CAPTION_SIZE)
    # The pictures the Boolean's parts embed for its current state, body first.
    pictures: tuple[PartPicture, ...] = ()

    def value_bounds(self, bounds: Rect) -> Rect:
        if self.native_size is None:
            return bounds
        return value_box_bounds(bounds, self.value_origin, self.native_size)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        button = scale_local(self.button_local, self.native_size, bounds)
        body = button if button is not None else bounds
        if not any(picture.is_body for picture in self.pictures):
            self._draw_kind(backend, body, bounds, theme)
        for picture in self.pictures:
            rect = scale_local(picture.rect, self.native_size, bounds)
            if rect is not None:
                x1, y1, x2, y2 = rect
                backend.image(picture.href, x1, y1, x2 - x1, y2 - y1, pixelated=False)

    def _draw_kind(
        self, backend: Backend, body: Rect, bounds: Rect, theme: Theme
    ) -> None:
        """The drawing of the Boolean's own kind, in its body rect."""
        color = self.state_color or (kinds.LED_ON if self.on else kinds.LED_OFF)
        kind = self.kind
        if kind == BooleanKind.ROUND_LED:
            bezel = scale_local(self.bezel_local, self.native_size, bounds)
            kinds.draw_round_led(backend, body, bezel, color)
        elif kind == BooleanKind.SLIDE_SWITCH:
            thumb = scale_local(self.thumb_local, self.native_size, bounds)
            kinds.draw_slide_switch(backend, body, thumb, self.thumb_color or color)
        elif kind == BooleanKind.MODERN_BUTTON:
            kinds.draw_modern_button(backend, body, self.caption, self.caption_style)
        elif kind == BooleanKind.SYSTEM_BUTTON:
            kinds.draw_system_button(backend, body, self.caption, self.caption_style)
        elif kind == BooleanKind.CHECK_BOX:
            kinds.draw_check_box(backend, body, self.on)
        elif kind == BooleanKind.RADIO:
            kinds.draw_radio_button(backend, body, self.on)
        elif kind == BooleanKind.SQUARE_LED:
            kinds.draw_square_led(backend, body, color)
        else:
            self._draw_generic(backend, body, theme)

    def _draw_generic(self, backend: Backend, body: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = body
        fill = theme.wire_bool if self.on else theme.fp_panel
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=fill,
            stroke=theme.struct_border,
            stroke_width=1.0,
            rx=(y2 - y1) / 2,
        )
        thumb_x = x2 - (y2 - y1) / 2 if self.on else x1 + (y2 - y1) / 2
        backend.circle(
            thumb_x,
            (y1 + y2) / 2,
            (y2 - y1) / 2 - 3,
            fill=theme.canvas,
            stroke=theme.struct_border,
            stroke_width=1.0,
        )


def _state_color(part: ParsedFPPart | None, on: bool) -> str | None:
    """The stored color of ``part``'s off (first) or on (second) state."""
    if part is None or len(part.states) < 2:
        return None
    return part.states[1 if on else 0].bg_color


def _caption(ctrl: ParsedFPControl, on: bool) -> tuple[str, TextStyle]:
    """The button text of the current state and its font and stored color. A
    part with one text shows it in both states."""
    part = find_part(ctrl, _CAPTION_PART_ID)
    if part is None or part.hidden or not part.texts:
        return "", TextStyle(kinds.CAPTION_SIZE)
    text = part.texts[min(1 if on else 0, len(part.texts) - 1)]
    return text, part_style(part, kinds.CAPTION_SIZE)


def _pictures(
    ctrl: ParsedFPControl, origin: tuple[float, float], on: bool
) -> tuple[PartPicture, ...]:
    """The pictures the shown parts of ``ctrl`` embed (a positive id), the one of
    the current state when the part has one per state (off first, then on), the
    body part first."""
    found = []
    for part in ctrl.parts:
        if part.hidden or not part.image_ids:
            continue
        index = min(1 if on else 0, len(part.image_ids) - 1)
        png = ctrl.pictures.get(part.image_ids[index])
        rect = local_part_rect(ctrl, part.part_id, origin) if part.part_id else None
        if png is None or rect is None:
            continue
        href = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        found.append(PartPicture(rect, href, part.part_id == BOOLEAN_BODY_PART_ID))
    return tuple(sorted(found, key=lambda picture: not picture.is_body))


def boolean_control(ctrl: ParsedFPControl) -> BooleanControlGlyph:
    """``ctrl``'s boolean glyph, laid out from its body part when present."""
    on = ctrl.default_value in ("True", "1")
    x1, y1, x2, y2 = value_extent(ctrl)
    origin = (x1, y1)
    button = local_part_rect(ctrl, BOOLEAN_BODY_PART_ID, origin)
    if button is None:
        return BooleanControlGlyph(on=on)
    body = find_part(ctrl, BOOLEAN_BODY_PART_ID)
    caption, caption_style = _caption(ctrl, on)
    return BooleanControlGlyph(
        on=on,
        value_origin=origin,
        native_size=(x2 - x1, y2 - y1),
        button_local=button,
        kind=boolean_kind(ctrl),
        bezel_local=local_part_rect(ctrl, _BEZEL_PART_ID, origin),
        thumb_local=local_part_rect(ctrl, _THUMB_PART_ID, origin),
        state_color=_state_color(body, on),
        thumb_color=_state_color(find_part(ctrl, _THUMB_PART_ID), on),
        caption=caption,
        caption_style=caption_style,
        pictures=_pictures(ctrl, origin, on),
    )
