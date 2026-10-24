from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme

_MARK_W = 3.0  # arrow width, in diagram units
_MARK_H = 4.0  # arrow height


@dataclass(frozen=True)
class ExtFuncGlyph:
    """A Call Library Function Node (heap class ``extFunc``): a stack of rows,
    one each for the path pair, the error pair, the return value and every
    parameter. An input terminal's arrow sits on its row's left edge, an
    output terminal's on the right edge. The node draws no text -- its name is
    in the tooltip.

    ``rows`` are ``(top, bottom, has_input, has_output)`` with the span as a
    fraction of the node's height, from the terminals' own geometry."""

    rows: tuple[tuple[float, float, bool, bool], ...] = ()
    fill_attr: str = "const_fill"
    stroke_attr: str = "tunnel_border"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=stroke,
            stroke_width=1.2,
        )
        height = y2 - y1
        for i, (top, bottom, has_input, has_output) in enumerate(self.rows):
            ry1, ry2 = y1 + top * height, y1 + bottom * height
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=0.6)
            cy = (ry1 + ry2) / 2
            half = min(_MARK_H, ry2 - ry1) / 2
            if has_input:
                backend.polygon(
                    [
                        (x1 + 1.0, cy - half),
                        (x1 + 1.0 + _MARK_W, cy),
                        (x1 + 1.0, cy + half),
                    ],
                    fill=stroke,
                    stroke="none",
                )
            if has_output:
                backend.polygon(
                    [
                        (x2 - 1.0 - _MARK_W, cy - half),
                        (x2 - 1.0, cy),
                        (x2 - 1.0 - _MARK_W, cy + half),
                    ],
                    fill=stroke,
                    stroke="none",
                )
