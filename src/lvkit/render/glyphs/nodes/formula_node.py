from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _truncate_to_width


@dataclass(frozen=True)
class FormulaNodeGlyph:
    """A Formula Node (fBox): a flat, structure-styled box (same outline
    treatment as a Sequence/Case structure — see ``draw.py::draw_structure``
    and ``Theme.struct_border``) holding its embedded C-like ``script`` as
    plain MONOSPACE text, left- and top-aligned, one line per source line.

    Unlike a real structure, a Formula Node's box is drawn as an ordinary
    node glyph (not via ``draw_structure``'s wire-safe outline-only pass)
    because nothing ever routes THROUGH its interior — every variable is a
    named tunnel on the border (drawn separately, in ``draw.py``, where the
    node's terminals are available) — so an opaque fill is safe here and
    keeps the script text readable over whatever sits behind the node.

    The script's LEADING blank lines (the decoded XML often opens with a
    few) are stripped so the first real line of code sits at the top
    padding; INTERIOR blank lines are kept so the script's own line breaks
    are preserved. A line that would fall past the box's bottom edge is
    simply not drawn — no shrinking or wrapping, matching how LabVIEW itself
    just clips a Formula Node's script to its stored size."""

    script: str
    fill_attr: str = "canvas"
    stroke_attr: str = "struct_border"
    stroke_width: float = 1.5
    text_size: float = 7.5
    text_attr: str = "text"
    pad: float = 7.0

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        self.draw_box(backend, bounds, theme)
        self.draw_script(backend, bounds, theme, self.pad, self.pad)

    def draw_box(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
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

    def draw_script(
        self,
        backend: Backend,
        bounds: Rect,
        theme: Theme,
        left_inset: float,
        right_inset: float,
    ) -> None:
        """Draw the C source as monospace text, inset from the box's LEFT and
        RIGHT edges by the given amounts so it clears the input-tunnel column on
        the left and the output-tunnel column on the right (roadmap #61 — the
        variables live on the border, so the code must not run under them). Each
        line is truncated to the remaining width; lines past the bottom edge are
        clipped, matching how LabVIEW clips a Formula Node's script to its size."""
        x1, y1, x2, y2 = bounds
        lines = self.script.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            return
        tx = x1 + left_inset
        max_w = (x2 - right_inset) - tx
        if max_w <= 4:
            return
        line_h = self.text_size + 2.0
        ty = y1 + self.pad + self.text_size
        text_fill = getattr(theme, self.text_attr)
        bottom = y2 - self.pad * 0.5
        for line in lines:
            if ty > bottom:
                break
            s = _truncate_to_width(backend, line, self.text_size, max_w)
            if s:
                backend.text(
                    tx,
                    ty,
                    s,
                    self.text_size,
                    anchor="start",
                    fill=text_fill,
                    mono=True,
                )
            ty += line_h
