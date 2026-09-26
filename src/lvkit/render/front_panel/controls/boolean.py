"""A front-panel boolean control."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


@dataclass(frozen=True)
class BooleanControlGlyph:
    """A front-panel boolean control: a slide-switch track + thumb, clean-room
    original chrome (never a copy of LabVIEW's own switch art) -- a different,
    correct visual metaphor from ``BooleanConstantGlyph``'s fixed push-button
    T/F box, which a CONSTANT (never resized, never "on/off" as a control is)
    always uses regardless of style."""

    on: bool

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        fill = theme.wire_bool if self.on else theme.fp_panel
        backend.rect(x1, y1, x2, y2, fill=fill, stroke=theme.struct_border,
                     stroke_width=1.0, rx=(y2 - y1) / 2)
        thumb_x = x2 - (y2 - y1) / 2 if self.on else x1 + (y2 - y1) / 2
        backend.circle(thumb_x, (y1 + y2) / 2, (y2 - y1) / 2 - 3, fill=theme.canvas,
                        stroke=theme.struct_border, stroke_width=1.0)
