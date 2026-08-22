from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _draw_drawer_row, fit_label


@dataclass(frozen=True)
class InvokeNodeGlyph:
    """An Invoke Node (heap class ``invokeNode``): like a Property Node, but the
    invoked METHOD name is the first drawer row, and the method's parameters
    grow DOWNWARD beneath it. The reference (in/out) and error (in/out)
    terminals thread the header edges and are placed by the scene.

    Row count and content come from the heap's ``dcoList`` (method + params,
    NOT one row per raw terminal — see ``render/nodes.py:_invoke_node_glyph``):
    the method row's left side is always a Void select-slot, so it never draws
    an arrow -- just the method name; its right side is the return value,
    drawn with the shared arrow rule (``▸`` right, only if present). Parameter
    NAMES aren't in the VI file (they belong to the method's VI-server
    signature), so param rows are labeled by index (``[i]``); each side draws
    its arrow via the same rule -- a plain input gets a left ``▸``, a plain
    output a right ``▸``, and a pass-through param (both present) gets both.
    Scales to the node's heap bounds."""

    method: str = ""
    return_present: bool = False  # method row's return-value terminal (right ▸)
    # (param label, show_left, show_right)
    rows: tuple[tuple[str, bool, bool], ...] = ()
    class_name: str = ""  # object class shown in the header (e.g. "VI")
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=stroke,
            stroke_width=1.2,
        )
        rows = self.rows
        # header (class) + method row + one cell per parameter row.
        cell_h = (y2 - y1) / (len(rows) + 2)
        lpad = 3.0
        lsize = max(5.0, min(9.0, cell_h * 0.62) - 1.0)

        # Header band: the object class, centered, with a divider beneath.
        hy2 = y1 + cell_h
        header = f"⚙ {self.class_name}".strip() if self.class_name else "⚙ class"
        backend.text(
            (x1 + x2) / 2,
            y1 + cell_h / 2 + lsize * 0.34,
            fit_label(header, (x2 - x1) - 2 * lpad, backend, lsize),
            lsize,
            fill=text_fill,
        )
        backend.line(x1, hy2, x2, hy2, stroke=stroke, stroke_width=1.0)

        # Method row: the invoked method name, never a left arrow (that side is
        # always a Void select-slot); the return value (if any) draws right.
        my2 = hy2 + cell_h
        method = self.method or "method"
        _draw_drawer_row(
            backend,
            x1,
            x2,
            hy2,
            my2,
            method,
            show_left=False,
            show_right=self.return_present,
            text_fill=text_fill,
            lsize=lsize,
        )
        backend.line(x1, my2, x2, my2, stroke=stroke, stroke_width=1.0)

        # Parameter drawer, one row per dcoList param pair below the method.
        for i, (name, show_left, show_right) in enumerate(rows):
            ry1 = my2 + i * cell_h
            ry2 = my2 + (i + 1) * cell_h
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=1.0)
            _draw_drawer_row(
                backend,
                x1,
                x2,
                ry1,
                ry2,
                name,
                show_left=show_left,
                show_right=show_right,
                text_fill=text_fill,
                lsize=lsize,
            )
