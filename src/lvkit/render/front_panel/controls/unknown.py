"""The fallback for a control type outside the renderer's scope."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...glyph import fit_label
from ...style import Theme


@dataclass(frozen=True)
class UnknownControlGlyph:
    """A control type outside the renderer's scope (listbox, tab control,
    waveform graph, ...) -- a labeled dashed fallback box, matching this
    project's existing convention for an unresolved primitive: visible,
    never silently dropped."""

    control_type: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(x1, y1, x2, y2, fill=theme.canvas, stroke=theme.struct_border,
                     stroke_width=1.0, stroke_dasharray="3,2")
        fitted = fit_label(self.control_type, x2 - x1 - 6, backend, 10.0)
        backend.text((x1 + x2) / 2, (y1 + y2) / 2 + 3, fitted, 10.0,
                     fill=theme.pane_type_text)
