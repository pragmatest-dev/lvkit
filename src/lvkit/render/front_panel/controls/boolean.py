"""A front-panel boolean control, drawn from its own heap parts.

The heap's value box is the union of the boolean's parts -- the button face,
its shadow and the divot around it -- so the control is drawn in the body part's
rect (``partID`` 21), not the whole box. Each kind of Boolean (``style.
BooleanKind``) has its own drawing (``boolean_kinds.py``); a Boolean whose body
is none of them, and one with no heap parts, is a slide-switch track and thumb
that fills the box it is given.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl, ParsedFPPart
from ...backend import Backend
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
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
    caption_color: str | None = None

    def value_bounds(self, bounds: Rect) -> Rect:
        if self.native_size is None:
            return bounds
        return value_box_bounds(bounds, self.value_origin, self.native_size)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        button = scale_local(self.button_local, self.native_size, bounds)
        body = button if button is not None else bounds
        color = self.state_color or (kinds.LED_ON if self.on else kinds.LED_OFF)
        kind = self.kind
        if kind == BooleanKind.ROUND_LED:
            bezel = scale_local(self.bezel_local, self.native_size, bounds)
            kinds.draw_round_led(backend, body, bezel, color)
        elif kind == BooleanKind.SLIDE_SWITCH:
            thumb = scale_local(self.thumb_local, self.native_size, bounds)
            kinds.draw_slide_switch(
                backend, body, thumb, self.thumb_color or color
            )
        elif kind == BooleanKind.MODERN_BUTTON:
            kinds.draw_modern_button(backend, body, self.caption, self.caption_color)
        elif kind == BooleanKind.SYSTEM_BUTTON:
            kinds.draw_system_button(backend, body, self.caption, self.caption_color)
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
        backend.rect(x1, y1, x2, y2, fill=fill, stroke=theme.struct_border,
                     stroke_width=1.0, rx=(y2 - y1) / 2)
        thumb_x = x2 - (y2 - y1) / 2 if self.on else x1 + (y2 - y1) / 2
        backend.circle(thumb_x, (y1 + y2) / 2, (y2 - y1) / 2 - 3, fill=theme.canvas,
                        stroke=theme.struct_border, stroke_width=1.0)


def _state_color(part: ParsedFPPart | None, on: bool) -> str | None:
    """The stored color of ``part``'s off (first) or on (second) state."""
    if part is None or len(part.states) < 2:
        return None
    return part.states[1 if on else 0].bg_color


def _caption(ctrl: ParsedFPControl, on: bool) -> tuple[str, str | None]:
    """The button text of the current state and its stored color. A part with
    one text shows it in both states."""
    part = find_part(ctrl, _CAPTION_PART_ID)
    if part is None or part.hidden or not part.texts:
        return "", None
    return part.texts[min(1 if on else 0, len(part.texts) - 1)], part.text_color


def boolean_control(ctrl: ParsedFPControl) -> BooleanControlGlyph:
    """``ctrl``'s boolean glyph, laid out from its body part when present."""
    on = ctrl.default_value in ("True", "1")
    x1, y1, x2, y2 = value_extent(ctrl)
    origin = (x1, y1)
    button = local_part_rect(ctrl, BOOLEAN_BODY_PART_ID, origin)
    if button is None:
        return BooleanControlGlyph(on=on)
    body = find_part(ctrl, BOOLEAN_BODY_PART_ID)
    caption, caption_color = _caption(ctrl, on)
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
        caption_color=caption_color,
    )
