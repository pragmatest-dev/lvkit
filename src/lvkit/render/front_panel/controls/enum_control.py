"""A front-panel ring/enum control."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...glyph import fit_label
from ...style import Theme


@dataclass(frozen=True)
class EnumControlGlyph:
    """A front-panel ring/enum control: a value cell showing the selected
    item's real label + a dropdown chevron -- chrome a block-diagram enum
    constant never needs."""

    default_value: str | None
    enum_values: tuple[str, ...]

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        idx = 0
        try:
            idx = int(self.default_value or 0)
        except ValueError:
            idx = 0
        text = (
            self.enum_values[idx]
            if self.enum_values and 0 <= idx < len(self.enum_values)
            else (self.default_value or "")
        )
        backend.rect(x1, y1, x2, y2, fill=theme.fp_value_fill,
                     stroke=theme.struct_border, stroke_width=1.0)
        text_start = x1 + 6
        cx = x2 - 10
        cy = (y1 + y2) / 2
        # Budget derived from the SAME geometry the chevron below actually
        # draws at (chevron's own left edge is cx - 4), plus a 2px buffer for
        # `fit_label`'s text-measurement approximation (a per-glyph average
        # em-width table -- close but not exact for every real string, e.g.
        # "20 MHz" measured a few px narrower than cairosvg actually renders
        # it) -- rather than a second, independently-drifting magic number.
        fitted = fit_label(text, (cx - 4) - text_start - 2, backend, 11.0)
        backend.text(text_start, cy + 4, fitted, 11.0, fill=theme.fp_value_text,
                     anchor="start")
        backend.polygon([(cx - 4, cy - 2), (cx + 4, cy - 2), (cx, cy + 3)],
                         fill=theme.struct_border)
