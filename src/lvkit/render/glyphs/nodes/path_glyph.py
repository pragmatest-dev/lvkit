"""``PathGlyph`` — a LabVIEW Path constant: a clean-room FOLDER mark (our own
outline, never traced NI artwork) plus the path text when set, so a path
control is visually identifiable even when its value is empty/default —
never a featureless colored rectangle (issue #45's blank-box fix: a value
glyph must ALWAYS carry a type-identifying visual, not just a border color)."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import fit_label


@dataclass(frozen=True)
class PathGlyph:
    """``value`` is the path text (empty for an unset/default path — the
    folder mark alone still identifies the type). ``color`` is the path's
    own wire color (``style.wire_path``)."""

    value: str
    color: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2, fill=theme.const_fill, stroke=self.color, stroke_width=1.2,
        )
        w, h = x2 - x1, y2 - y1
        pad = 2.5
        s = min(w - 2 * pad, h - 2 * pad, max(8.0, h * 0.5))
        if s > 5.0:
            self._draw_folder(backend, x1 + pad, y1 + pad, s, self.color)
            text_y = min(y1 + pad + s + self.color_text_gap(s), y2 - 2.0)
            text_x = x1 + pad
        else:
            text_y = (y1 + y2) / 2 + 3.0
            text_x = x1 + pad
        if self.value:
            size = min(8.0, h * 0.4)
            label = fit_label(self.value, w - 2 * pad, backend, size)
            if label:
                backend.text(
                    text_x, text_y, label, size, anchor="start", fill=self.color,
                )

    @staticmethod
    def color_text_gap(folder_size: float) -> float:
        return folder_size * 0.35 + 6.0

    def _draw_folder(
        self, backend: Backend, x: float, y: float, s: float, color: str
    ) -> None:
        """A simple folder-with-tab silhouette outline — the classic clean-
        room "this is a path/file" mark."""
        tab_h = s * 0.22
        tab_w = s * 0.55
        notch = s * 0.12
        backend.polygon(
            [
                (x, y + tab_h),
                (x + tab_w, y + tab_h),
                (x + tab_w + notch, y),
                (x + s, y),
                (x + s, y + s * 0.8),
                (x, y + s * 0.8),
            ],
            fill="none", stroke=color, stroke_width=1.1,
        )
