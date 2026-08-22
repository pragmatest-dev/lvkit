from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import fit_wrapped


@dataclass(frozen=True)
class WrappedBoxGlyph:
    """A subVI box with its NAME wrapped inside — LabVIEW's default look for a
    subVI that has no custom icon. The name word-wraps to at most ``max_lines``
    (default 4) lines sized to the box width, vertically centered; an overlong
    name is hard-broken and ellipsized. Used instead of drawing an empty box +
    a label underneath, so the name lives in the icon space itself."""

    label: str
    fill_attr: str = "subvi_fill"
    stroke_attr: str = "subvi_stroke"
    stroke_width: float = 1.5
    max_lines: int = 4
    text_size: float = 7.0
    text_attr: str = "subvi_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=getattr(theme, self.stroke_attr),
            stroke_width=self.stroke_width,
        )
        self.draw_wrapped_text(backend, bounds, theme)

    def draw_wrapped_text(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """Draw only the word-wrapped, best-fit-sized label centered in
        ``bounds`` (no box) — shared by ``draw`` and by other glyphs that own
        their own frame (e.g. ``LocalVariableGlyph``, which draws a badge in a
        left cell and delegates the remaining width to this)."""
        x1, y1, x2, y2 = bounds
        if not self.label:
            return
        pad = 2.0
        avail_w = (x2 - x1) - 2 * pad
        avail_h = (y2 - y1) - 2 * pad
        if avail_w <= 2 or avail_h <= 2:
            return
        size, line_h, lines = self._best_fit(avail_w, avail_h, backend)
        if not lines:
            return
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        # Center the baseline set vertically on cy (small +size*0.32 nudge so
        # the visual mass, not the baselines, sits centered).
        first = cy - (len(lines) - 1) * line_h / 2 + size * 0.32
        text_fill = getattr(theme, self.text_attr)
        for i, line in enumerate(lines):
            backend.text(cx, first + i * line_h, line, size, fill=text_fill)

    def _best_fit(
        self,
        avail_w: float,
        avail_h: float,
        backend: Backend,
    ) -> tuple[float, float, list[str]]:
        return fit_wrapped(
            self.label,
            avail_w,
            avail_h,
            backend,
            self.text_size,
            self.max_lines,
        )
