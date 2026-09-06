from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ....parser.layout import ClusterFieldGeom, Rect
from ...backend import Backend
from ...style import Theme
from .base import Glyph


@dataclass(frozen=True)
class ClusterConstantGlyph:
    """A cluster constant drawn by COMPOSING each field's own constant glyph
    (boolean / numeric / string / …) inside a cluster box.

    When ``field_geom`` carries every field's real heap geometry (see
    ``ClusterFieldGeom`` / ``layout._cluster_field_geoms``), each field draws
    at its own REAL value/label rect — its real size, real position, no
    stretching. Otherwise (no geometry — an older ``Layout``, or a heap shape
    this pass couldn't decode) fields fall back to a vertical stack of equal-
    height "name: value" rows fit to the box. Error clusters get the mustard
    border (``wire_error``) and the stored status / code / source field
    order; any other cluster gets the generic cluster brown."""

    fields: tuple[tuple[str, Glyph], ...]
    is_error: bool = False
    # Drawn COLLAPSED ("View As Icon") — show the compact cluster icon, never the
    # members. A collapsed constant's box is deliberately too small for content;
    # LabVIEW cannot shrink a value's natural height, so a small box is always a
    # collapse, never squashed members. (``ConstantNode.collapsed``.)
    collapsed: bool = False
    fill_attr: str = "const_fill"
    # The cluster's own wire color (brown for an all-numeric cluster, pink for a
    # mixed/common one) — the icon border matches the wire. None -> brown.
    border_color: str | None = None
    # ``name: value`` per field, for a hover tooltip — useful when the cluster
    # is drawn small/collapsed and the inline values aren't legible.
    value_summary: str = ""
    # Field name -> its REAL heap geometry (see class docstring). Empty for a
    # cluster the heap-geometry pass couldn't decode — the equal-height-row
    # fallback below then applies to every field.
    field_geom: Mapping[str, ClusterFieldGeom] = field(default_factory=dict)

    # Below these, a stacked "name: value" row can't fit both a name AND a
    # value cell, so we drop the field-NAME labels and draw the field VALUES
    # alone (stacked, scaled to the box) — never a blank box, since LabVIEW
    # always shows a cluster constant's contents (the names are the toggleable
    # part; see the field-label discussion). Names stay on the hover tooltip
    # (``value_summary``). These are legibility floors tied to ``label_size``
    # — NOT a semantic "collapsed" flag, which the heap does not carry.
    _MIN_ROW_H = 9.0
    _MIN_FIELD_W = 40.0

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        if self.is_error:
            border = theme.wire_error
        else:
            border = self.border_color or theme.wire_cluster
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=border,
            stroke_width=1.5,
        )
        if self.collapsed or not self.fields:
            # COLLAPSED ("View As Icon"), or a genuinely unresolved / empty
            # cluster (no field info): the compact cluster icon, never squashed
            # members or a raw value repr.
            self._draw_generic_icon(backend, bounds, theme)
            return
        if self.field_geom and all(name in self.field_geom for name, _ in self.fields):
            self._draw_real_geometry(backend, theme, border)
            return
        pad = 3.0
        label_size = 7.0
        row_h = (y2 - y1 - 2 * pad) / len(self.fields)
        if row_h < self._MIN_ROW_H or (x2 - x1 - 2 * pad) < self._MIN_FIELD_W:
            # Too small for name+value rows: draw the field VALUE glyphs alone,
            # fit to the box. A one-boolean cluster shows its boolean; a small
            # multi-field cluster shows its members' value glyphs — the REAL
            # content and field count, never a generic mixed-element icon that
            # misstates them. Names stay on the hover tooltip. Fits inside the
            # given box, so it also composes into an array-of-clusters' per-
            # element cell (LabVIEW lays the element cluster out in that box).
            self._draw_value_cells(backend, bounds, theme)
            return
        self._draw_labeled_rows(backend, bounds, theme, border, pad, label_size)

    def _draw_real_geometry(self, backend: Backend, theme: Theme, border: str) -> None:
        """Draw each field at its OWN real heap rect — real size, real
        position, no row stretch. A field's name draws at its real label
        rect (skipped when the heap has the caption hidden, i.e.
        ``label_rect is None``)."""
        label_size = 7.0
        for name, field_glyph in self.fields:
            geom = self.field_geom[name]
            vx1, vy1, vx2, vy2 = geom.value_rect
            if vx2 > vx1 and vy2 > vy1:
                field_glyph.draw(backend, geom.value_rect, theme)
            if geom.label_rect is not None:
                lx1, ly1, lx2, ly2 = geom.label_rect
                if lx2 > lx1 and ly2 > ly1:
                    backend.text(
                        lx1 + 1.0,
                        (ly1 + ly2) / 2 + label_size * 0.34,
                        name,
                        label_size,
                        anchor="start",
                        fill=border,
                    )

    def _draw_value_cells(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """Stack each field's VALUE glyph (no name label) to fill the box — one
        equal-height row per field. The field glyphs draw themselves at whatever
        size the cell gives, so this fits any box down to a single-member
        square."""
        x1, y1, x2, y2 = bounds
        pad = 1.5
        n = len(self.fields)
        cell_h = (y2 - y1 - 2 * pad) / n
        for i, (_name, field_glyph) in enumerate(self.fields):
            cy1 = y1 + pad + i * cell_h
            field_glyph.draw(backend, (x1 + pad, cy1, x2 - pad, cy1 + cell_h), theme)

    def _draw_generic_icon(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """The fallback shell icon for a cluster whose fields could NOT be
        resolved (no composed field glyphs) — a couple of small element squares
        inside the shell, so an unresolved cluster still reads as a cluster
        rather than an empty box. A cluster WITH fields draws its real members
        (:meth:`_draw_value_cells`) instead."""
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        s = max(2.0, min(w, h) * 0.22)  # element-square size
        gap = s * 0.5
        colors = (theme.wire_string, theme.wire_int)
        ex = x1 + w * 0.22
        ey = y1 + h * 0.28
        for i, col in enumerate(colors):
            bx = ex + i * (s + gap)
            backend.rect(bx, ey, bx + s, ey + s, fill=col, stroke="none")
        backend.rect(ex, ey + s + gap, ex + s, ey + 2 * s + gap,
                     fill=theme.wire_bool, stroke="none")

    def _draw_labeled_rows(
        self, backend: Backend, bounds: Rect, theme: Theme,
        border: str, pad: float, label_size: float,
    ) -> None:
        x1, y1, x2, y2 = bounds
        row_h = (y2 - y1 - 2 * pad) / len(self.fields)
        label_w = min(
            0.4 * (x2 - x1),
            max(backend.measure_text(nm, label_size) for nm, _ in self.fields) + 4.0,
        )
        for i, (name, field_glyph) in enumerate(self.fields):
            ry1 = y1 + pad + i * row_h
            ry2 = ry1 + row_h
            backend.text(
                x1 + pad,
                (ry1 + ry2) / 2 + label_size * 0.34,
                name,
                label_size,
                anchor="start",
                fill=border,
            )
            cx1 = x1 + pad + label_w
            if x2 - pad > cx1 and ry2 - 1.0 > ry1 + 1.0:
                field_glyph.draw(backend, (cx1, ry1 + 1.0, x2 - pad, ry2 - 1.0), theme)
