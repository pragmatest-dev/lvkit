from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import wrap_label


@dataclass(frozen=True)
class LabelGlyph:
    """A free-standing block-diagram comment -- LabVIEW's "Free Label"
    decoration, distinct from a node's own label/caption. Renders as a
    filled box with top-left word-wrapped text, the same layout as
    ``ConstantGlyph``'s ``multiline`` mode. ``bg_color`` is the label's own
    heap color (``00RRGGBB`` hex, e.g. LabVIEW's default pale yellow
    ``00FFFFD7``); a neutral (canvas) fill is used when absent."""

    text: str
    bg_color: str | None = None
    text_size: float = 9.0

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        fill = self._css_fill(theme)
        # A transparent free label (LabVIEW alpha byte 01) has NO box at all —
        # text only over whatever is behind it (see draw.py::_draw_owned_label).
        # Drawing the box would paint its meaningless RGB (usually 000000 → a
        # solid black rectangle over the diagram/wire).
        if fill is not None:
            backend.rect(
                x1,
                y1,
                x2,
                y2,
                fill=fill,
                stroke=theme.struct_border,
                stroke_width=1.0,
            )
        if self.text:
            self._draw_wrapped(backend, x1, y1, x2, y2, theme)

    def _css_fill(self, theme: Theme) -> str | None:
        """The label's own heap background color, a neutral fallback, or None
        when the label is TRANSPARENT.

        ``bg_color`` is ``"AARRGGBB"``: the leading ALPHA byte is ``00`` for an
        opaque colored label and non-zero (``01``) for LabVIEW's transparent
        flag, where the trailing RGB is meaningless (e.g. ``01000000`` /
        ``0100000A``). Return None for transparent so the caller draws no box."""
        if self.bg_color and len(self.bg_color) >= 6:
            if len(self.bg_color) >= 8 and self.bg_color[:2] != "00":
                return None
            return f"#{self.bg_color[-6:]}"
        return theme.canvas

    def _draw_wrapped(
        self,
        backend: Backend,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        theme: Theme,
    ) -> None:
        pad = 2.5
        avail_w = x2 - x1 - 2 * pad
        line_h = self.text_size + 2.0
        max_lines = max(1, int((y2 - y1 - 2 * pad) / line_h))
        # Honor explicit newlines first, then word-wrap each segment (mirrors
        # ConstantGlyph._draw_wrapped).
        lines: list[str] = []
        for seg in self.text.split("\n"):
            if not seg:
                lines.append("")
                continue
            lines.extend(wrap_label(seg, avail_w, backend, self.text_size, max_lines))
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            last = lines[-1]
            while last and backend.measure_text(last + "…", self.text_size) > avail_w:
                last = last[:-1]
            lines[-1] = last + "…"
        ty = y1 + pad + self.text_size
        text_fill = theme.text
        for line in lines:
            backend.text(
                x1 + pad,
                ty,
                line,
                self.text_size,
                anchor="start",
                fill=text_fill,
            )
            ty += line_h
