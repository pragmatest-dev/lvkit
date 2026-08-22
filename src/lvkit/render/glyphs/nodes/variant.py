from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


@dataclass(frozen=True)
class VariantGlyph:
    """A LabVIEW Variant: opaque, type-erased data — the schematic is a
    SOLID filled box (no internal detail, since there IS no visible
    internal structure to a Variant), sharp corners like every other
    block-diagram object."""

    fill_attr: str = "wire_variant"
    stroke_attr: str = "struct_border"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=1.0,
        )
