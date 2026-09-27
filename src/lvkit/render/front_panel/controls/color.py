"""The value glyph for a ``stdColorNum`` (Color Box) control."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme


@dataclass(frozen=True)
class ColorControlGlyph:
    """A solid swatch of the control's own recorded color -- ``color`` is
    ``#RRGGBB``, decoded from the heap's saved value the SAME ``00RRGGBB``
    packing ``parser.utils.heap_color`` already reads for a part's own
    ``fgColor``/``bgColor`` (verified against real corpus bytes: a Color Box's
    ``DefaultData`` is the same 4-byte ``00 RR GG BB``). ``None`` when the
    control has no saved default -- still a bordered box, not silently
    blank, with the SAME recessed-cell fill every other unset front-panel
    value uses (never ``theme.canvas``, which is the UnknownControlGlyph
    "outside the renderer's scope" convention -- a Color Box IS recognized,
    it just has nothing saved)."""

    color: str | None
    border_color: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=self.color or theme.fp_value_fill,
            stroke=self.border_color,
            stroke_width=1.0,
        )
