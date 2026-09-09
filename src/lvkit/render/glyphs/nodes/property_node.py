from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import DrawerHeaderGlyphBase, _draw_drawer_row, draw_drawer_header


@dataclass(frozen=True)
class PropertyNodeGlyph(DrawerHeaderGlyphBase):
    """A Property Node (heap class ``propNode``), matching LabVIEW's layout: a
    HEADER band (see ``DrawerHeaderGlyphBase``/``draw_drawer_header`` for the
    implicit/explicit contract, task #51 / reference image #69) above a
    DRAWER of one rectangle per accessed property.

    Each drawer row is LABELED with the property's NAME and marked with the
    shared arrow rule (see ``_draw_drawer_row``): read (value flows OUT) draws
    ``▸`` at the RIGHT edge, write (value flows IN) draws it at the LEFT edge.
    A property is read-or-write in practice, so a row draws at most one arrow.
    Names come from the node's ``properties`` list; the per-row direction from
    the matching value terminal. Grows with the property count, scaling to the
    node's heap bounds."""

    rows: tuple[tuple[str, bool], ...] = ()  # (property name, is_read)

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
        lsize = max(5.0, min(9.0, cell_h * 0.62) - 1.0)

        hy2 = draw_drawer_header(
            backend,
            x1,
            x2,
            y1,
            cell_h,
            is_implicit=self.is_implicit,
            target_name=self.target_name,
            class_name=self.class_name,
            bar_color=self.bar_color,
            stroke=stroke,
            text_fill=text_fill,
            lsize=lsize,
        )

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
