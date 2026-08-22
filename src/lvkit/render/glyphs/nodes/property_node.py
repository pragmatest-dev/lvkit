from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _draw_drawer_row, fit_label


@dataclass(frozen=True)
class PropertyNodeGlyph:
    """A Property Node (heap class ``propNode``), matching LabVIEW's layout: a
    HEADER band naming the object CLASS the reference is of (``⚙ <class>``), above
    a DRAWER of one rectangle per accessed property. The reference (in/out) and
    error (in/out) terminals thread the box edges at the header level and are
    placed by the scene, not drawn here.

    Each drawer row is LABELED with the property's NAME and marked with the
    shared arrow rule (see ``_draw_drawer_row``): read (value flows OUT) draws
    ``▸`` at the RIGHT edge, write (value flows IN) draws it at the LEFT edge.
    A property is read-or-write in practice, so a row draws at most one arrow.
    Names come from the node's ``properties`` list; the per-row direction from
    the matching value terminal. Grows with the property count, scaling to the
    node's heap bounds."""

    rows: tuple[tuple[str, bool], ...]  # (property name, is_read)
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
        rows = self.rows or (("", True),)
        # One header cell (the class/reference row) + one cell per property.
        cell_h = (y2 - y1) / (len(rows) + 1)
        lpad = 3.0
        lsize = max(5.0, min(9.0, cell_h * 0.62) - 1.0)

        # Header band: the object class, centered, with a divider beneath. This
        # is the row the reference + error terminals thread through (drawn by the
        # scene at the box edges).
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

        # Property drawer: one named rectangle per property, below the header.
        for i, (name, is_read) in enumerate(rows):
            ry1 = hy2 + i * cell_h
            ry2 = hy2 + (i + 1) * cell_h
            if i > 0:
                backend.line(x1, ry1, x2, ry1, stroke=stroke, stroke_width=1.0)
            _draw_drawer_row(
                backend,
                x1,
                x2,
                ry1,
                ry2,
                name,
                show_left=not is_read,
                show_right=is_read,
                text_fill=text_fill,
                lsize=lsize,
            )
