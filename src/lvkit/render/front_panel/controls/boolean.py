"""A front-panel boolean control, drawn from its own heap parts.

The heap's value box is the union of the boolean's parts -- the button face,
its shadow and the divot around it -- so the switch is drawn in the button
part's rect (``partID`` 21), not the whole box. Without heap parts the switch
fills the box it is given.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl
from ...backend import Backend
from ...glyphs.nodes.local_rect import scale_local
from ...style import Theme
from .base import ControlGlyph, local_part_rect, value_box_bounds, value_extent

_BUTTON_PART_ID = 21


@dataclass(frozen=True)
class BooleanControlGlyph(ControlGlyph):
    """A front-panel boolean control: a slide-switch track + thumb, clean-room
    original chrome (never a copy of LabVIEW's own switch art) -- a different,
    correct visual metaphor from ``BooleanConstantGlyph``'s fixed push-button
    T/F box, which a CONSTANT (never resized, never "on/off" as a control is)
    always uses regardless of style.

    ``button_local`` is relative to the value box (origin ``value_origin``,
    size ``native_size``)."""

    on: bool
    value_origin: tuple[float, float] = (0.0, 0.0)
    native_size: tuple[float, float] | None = None
    button_local: Rect | None = None

    def value_bounds(self, bounds: Rect) -> Rect:
        if self.native_size is None:
            return bounds
        return value_box_bounds(bounds, self.value_origin, self.native_size)

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        button = scale_local(self.button_local, self.native_size, bounds)
        x1, y1, x2, y2 = button if button is not None else bounds
        fill = theme.wire_bool if self.on else theme.fp_panel
        backend.rect(x1, y1, x2, y2, fill=fill, stroke=theme.struct_border,
                     stroke_width=1.0, rx=(y2 - y1) / 2)
        thumb_x = x2 - (y2 - y1) / 2 if self.on else x1 + (y2 - y1) / 2
        backend.circle(thumb_x, (y1 + y2) / 2, (y2 - y1) / 2 - 3, fill=theme.canvas,
                        stroke=theme.struct_border, stroke_width=1.0)


def boolean_control(ctrl: ParsedFPControl) -> BooleanControlGlyph:
    """``ctrl``'s boolean glyph, laid out from its button part when present."""
    on = ctrl.default_value in ("True", "1")
    x1, y1, x2, y2 = value_extent(ctrl)
    origin = (x1, y1)
    button = local_part_rect(ctrl, _BUTTON_PART_ID, origin)
    if button is None:
        return BooleanControlGlyph(on=on)
    return BooleanControlGlyph(
        on=on, value_origin=origin, native_size=(x2 - x1, y2 - y1),
        button_local=button,
    )
