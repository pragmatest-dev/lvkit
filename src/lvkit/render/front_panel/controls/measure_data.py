"""The value glyph for a ``stdMeasureData`` (Measurement Data) control."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...glyph import fit_label
from ...style import Theme

_LABEL_SIZE = 10.0


@dataclass(frozen=True)
class MeasureDataGlyph:
    """A bordered box + the control's own real ``measure_flavor`` (e.g.
    ``"DigitalWaveform"``), the SAME field ``render/style.py`` already reads
    to give a Timestamp its own color/glyph on the block diagram -- parsed
    from the VCTP type descriptor's own ``Flavor``, not the FPHb heap. Real
    corpus data (8 instances checked) never decomposes into component
    fields (t0/dt/Y) at this type-model level, so this never fakes one --
    only the real flavor text, or a generic fallback name when the flavor
    could not be resolved (an unset/unresolvable VCTP), still a recognized
    bordered box rather than a silent blank."""

    border_color: str
    flavor: str | None

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=theme.fp_value_fill, stroke=self.border_color, stroke_width=1.0,
        )
        text = fit_label(
            self.flavor or "Measurement Data", x2 - x1 - 6, backend, _LABEL_SIZE
        )
        backend.text(
            (x1 + x2) / 2, (y1 + y2) / 2 + _LABEL_SIZE * 0.34, text, _LABEL_SIZE,
            fill=theme.pane_type_text,
        )
