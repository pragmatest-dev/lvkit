from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import fit_value, fit_wrapped, wrap_label


@dataclass(frozen=True)
class ConstantGlyph:
    """A constant's colored box (color from its own ``LVType``, computed by
    the resolver — a literal color, not a theme attribute, since it varies
    per-node rather than per-role).

    ``multiline`` (string constants) renders the FULL text word-wrapped to the
    box, honoring explicit newlines, top- and left-aligned like a LabVIEW
    string constant — LabVIEW already sizes these boxes to their content, so
    wrapping fills the box instead of collapsing a multi-line word list to one
    ellipsized line. Scalar constants stay single-line and centered."""

    value: str
    color: str
    fill_attr: str = "term_fill"
    text_size: float = 9.0
    multiline: bool = False
    # ``fit``: word-wrap AND shrink the text (best-fit, down to a legibility
    # floor) so the FULL value fills the box — for a class/refnum constant whose
    # class name is too long to truncate into one line but too meaningful to
    # clip. Centered, unlike the top-left ``multiline`` string layout.
    fit: bool = False
    text_attr: str = "const_text"

    def truncated_value(self, backend: Backend, bounds: Rect) -> str | None:
        """The FULL value, but ONLY when drawing it into ``bounds`` ellipsizes
        or clips it (so the caller can expose it as a hover tooltip). ``None``
        when the in-box text already shows the value in full — no redundant
        tooltip on short constants. Mirrors what ``draw`` fits into the box."""
        if not self.value:
            return None
        x1, y1, x2, y2 = bounds
        if self.fit:
            _, _, lines = fit_wrapped(
                self.value,
                x2 - x1 - 5,
                y2 - y1 - 5,
                backend,
                self.text_size,
                max_lines=4,
            )
            return self.value if (not lines or lines[-1].endswith("…")) else None
        if not self.multiline:
            fitted = fit_value(self.value, x2 - x1, backend, self.text_size)
            return self.value if fitted != self.value else None
        pad = 2.5
        avail_w = x2 - x1 - 2 * pad
        line_h = self.text_size + 2.0
        max_lines = max(1, int((y2 - y1 - 2 * pad) / line_h))
        lines: list[str] = []
        for seg in self.value.split("\n"):
            if not seg:
                lines.append("")
                continue
            lines.extend(wrap_label(seg, avail_w, backend, self.text_size, max_lines))
        # Truncated if whole lines were clipped OR a line got ellipsized to fit
        # (wrap_label caps at max_lines and adds the "…" itself, so the line count
        # alone under-detects — check for the ellipsis it produced).
        if len(lines) > max_lines or any(ln.endswith("…") for ln in lines):
            return self.value
        return None

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=theme.const_fill,
            stroke=self.color,
            stroke_width=1.2,
        )
        if not self.value:
            return
        if self.fit:
            self._draw_fit(backend, x1, y1, x2, y2, theme)
        elif self.multiline:
            self._draw_wrapped(backend, x1, y1, x2, y2, theme)
        else:
            backend.text(
                (x1 + x2) / 2,
                (y1 + y2) / 2 + 3,
                fit_value(self.value, x2 - x1, backend, self.text_size),
                self.text_size,
                fill=getattr(theme, self.text_attr),
            )

    def _draw_fit(
        self,
        backend: Backend,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        theme: Theme,
    ) -> None:
        """Wrapped + shrunk-to-fit, centered (see ``fit``)."""
        pad = 2.5
        size, line_h, lines = fit_wrapped(
            self.value,
            x2 - x1 - 2 * pad,
            y2 - y1 - 2 * pad,
            backend,
            self.text_size,
            max_lines=4,
        )
        if not lines:
            return
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        first = cy - (len(lines) - 1) * line_h / 2 + size * 0.32
        text_fill = getattr(theme, self.text_attr)
        for i, line in enumerate(lines):
            backend.text(cx, first + i * line_h, line, size, fill=text_fill)

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
        # Honor explicit newlines first, then word-wrap each segment. A blank
        # segment (e.g. the leading newline of a line-indexed word list) keeps
        # a blank row so line indices line up with the source string.
        lines: list[str] = []
        for seg in self.value.split("\n"):
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
        text_fill = getattr(theme, self.text_attr)
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
