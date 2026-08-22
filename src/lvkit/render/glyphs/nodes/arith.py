from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _OPERATOR_SYMBOL_SIZE


@dataclass(frozen=True)
class ArithGlyph:
    """The arithmetic/comparison-primitive triangle (Add/Subtract/Multiply/
    Divide/Increment/Decrement, and the comparison functions Equal?/Greater?/
    ...) with its operator symbol. LabVIEW draws all of these as the same
    borderless right-pointing triangle — only the interior symbol differs."""

    symbol: str
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.polygon(
            [(x1, y1), (x2, (y1 + y2) / 2), (x1, y2)],
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=1.2,
        )
        size = _OPERATOR_SYMBOL_SIZE
        cy = (y1 + y2) / 2
        backend.text(
            x1 + (x2 - x1) * 0.36,
            cy + size * 0.34,
            self.symbol,
            size,
            fill=getattr(theme, self.text_attr),
        )
