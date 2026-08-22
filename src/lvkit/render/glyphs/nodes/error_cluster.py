from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


@dataclass(frozen=True)
class ErrorClusterGlyph:
    """A LabVIEW error cluster ({status: bool, code: i32, source: string}):
    a mustard-bordered box with a round status LED (top-left) and two short
    horizontal bars beneath it standing in for the code/source fields."""

    fill_attr: str = "const_fill"
    stroke_attr: str = "wire_error"
    led_attr: str = "wire_bool"
    bar_attr: str = "wire_error"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=1.2,
        )
        w, h = x2 - x1, y2 - y1
        pad = max(1.5, min(w, h) * 0.15)
        # Status LED: a small round indicator at the top-left.
        r = max(1.0, min(w, h) * 0.16)
        cx, cy = x1 + pad + r, y1 + pad + r
        backend.circle(
            cx,
            cy,
            r,
            fill=getattr(theme, self.led_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=0.6,
        )
        # Code/source fields: two short horizontal bars below the LED.
        bar_color = getattr(theme, self.bar_attr)
        bar_x1 = x1 + pad
        bar_x2 = x2 - pad
        if bar_x2 <= bar_x1:
            return
        bar_h = max(1.0, min(h * 0.12, 3.0))
        gap = max(1.0, h * 0.10)
        bar1_y = cy + r + gap
        bar2_y = bar1_y + bar_h + gap
        if bar2_y + bar_h <= y2 - pad * 0.5:
            backend.rect(bar_x1, bar1_y, bar_x2, bar1_y + bar_h, fill=bar_color)
            backend.rect(bar_x1, bar2_y, bar_x2, bar2_y + bar_h, fill=bar_color)
