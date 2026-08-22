from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


@dataclass(frozen=True)
class BooleanConstantGlyph:
    """A LabVIEW Boolean constant — the modern (2010+) look shows only the
    CURRENT value:

    - **True**: a green button with a white bezel — green outer outline, a
      white inner outline, green fill, and a bold white ``T``.
    - **False**: a plain white box — green outline, white fill, green ``F``.

    Replaces showing the raw ``True``/``False`` text in a box."""

    value: bool
    green_attr: str = "wire_bool"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        # LabVIEW's Boolean constant is a small SQUARE — draw a centered square
        # (side = the smaller dimension) rather than stretching to fill a box
        # that may be wider (a resized/labeled constant).
        side = min(x2 - x1, y2 - y1)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        x1, y1, x2, y2 = cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2
        green = getattr(theme, self.green_attr)
        if self.value:
            # Green outer outline + green fill, then a white inner outline
            # (the bezel) inset within it, still green-filled — a white T.
            # Sharp corners: LabVIEW block-diagram objects aren't rounded.
            backend.rect(x1, y1, x2, y2, fill=green, stroke=green, stroke_width=1.0)
            inset = min(2.0, (x2 - x1) * 0.16, (y2 - y1) * 0.16)
            backend.rect(
                x1 + inset,
                y1 + inset,
                x2 - inset,
                y2 - inset,
                fill=green,
                stroke="#ffffff",
                stroke_width=1.0,
            )
            text_fill, letter = "#ffffff", "T"
        else:
            backend.rect(x1, y1, x2, y2, fill="#ffffff", stroke=green, stroke_width=1.2)
            text_fill, letter = green, "F"
        size = max(6.0, min(11.0, (y2 - y1) * 0.72, (x2 - x1) * 0.9))
        backend.text(
            (x1 + x2) / 2,
            (y1 + y2) / 2 + size * 0.34,
            letter,
            size,
            fill=text_fill,
            bold=True,
        )
