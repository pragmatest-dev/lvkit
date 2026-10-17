from __future__ import annotations

import math
from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .wrapped_box import WrappedBoxGlyph


@dataclass(frozen=True)
class GlobalVariableGlyph:
    """A Global Variable node: the same bold-bordered box as a local variable,
    holding the NAME of the Global VI's control it reads or writes, marked by a
    small globe (a circle with an equator and a meridian) instead of the local
    variable's triangle. The globe is our own clean-room shape.

    The mark sits on the DATAFLOW side like the local variable's badge: a READ
    is a source (data leaves to the right), so the globe is on the right edge; a
    WRITE is a sink (data enters from the left), so it is on the left edge.

    ``border_color`` is the control's DATA-TYPE wire color (None falls back to
    the neutral stroke), as for a local variable.
    """

    label: str
    is_write: bool = False
    border_color: str | None = None
    fill_attr: str = "localvar_fill"
    stroke_attr: str = "localvar_stroke"
    text_attr: str = "localvar_text"
    max_lines: int = 2
    text_size: float = 7.0

    _STROKE_W = 2.5
    _MARK_STROKE_W = 0.9
    _MERIDIAN_SEGMENTS = 16

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = self.border_color or getattr(theme, self.stroke_attr)
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=stroke,
            stroke_width=self._STROKE_W,
        )
        pad = 2.0
        cy = (y1 + y2) / 2
        diameter = min(10.0, (y2 - y1) - 2 * pad)
        left_pad = right_pad = 0.0
        if diameter >= 5.0 and (x2 - x1) > 2 * pad + diameter + 4.0:
            r = diameter / 2
            if self.is_write:
                cx = x1 + pad + 1.0 + r  # left input edge
                left_pad = pad + 1.0 + diameter
            else:
                cx = x2 - pad - 1.0 - r  # right output edge
                right_pad = pad + 1.0 + diameter
            self._draw_globe(backend, cx, cy, r, getattr(theme, self.text_attr))
        text_glyph = WrappedBoxGlyph(
            self.label,
            self.fill_attr,
            self.stroke_attr,
            max_lines=self.max_lines,
            text_size=self.text_size,
            text_attr=self.text_attr,
        )
        text_glyph.draw_wrapped_text(
            backend,
            (x1 + left_pad, y1, x2 - right_pad, y2),
            theme,
        )

    def _draw_globe(
        self, backend: Backend, cx: float, cy: float, r: float, color: str
    ) -> None:
        w = self._MARK_STROKE_W
        backend.circle(cx, cy, r, fill="none", stroke=color, stroke_width=w)
        backend.line(cx - r, cy, cx + r, cy, stroke=color, stroke_width=w)
        n = self._MERIDIAN_SEGMENTS
        meridian = [
            (
                cx + r * 0.45 * math.cos(2 * math.pi * i / n),
                cy + r * math.sin(2 * math.pi * i / n),
            )
            for i in range(n + 1)
        ]
        backend.path(meridian, stroke=color, stroke_width=w)
