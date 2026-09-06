from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import ClusterGeom, Rect
from ...backend import Backend
from ...style import Theme
from .base import Glyph


@dataclass(frozen=True)
class ClusterConstantGlyph:
    """A cluster constant drawn by COMPOSING each field's own constant glyph
    (boolean / numeric / string / …) inside a cluster box.

    When ``cluster_geom`` carries the cluster's real heap geometry (see
    ``ClusterGeom`` / ``layout._cluster_field_geoms``), each field draws at
    its own REAL value/label rect — fit into ``bounds`` by a single uniform
    scale (never a per-axis stretch; 1.0 whenever ``bounds`` is already the
    cluster's own real box, which is the common case). A field that is
    itself a cluster (composed recursively by the resolver, see
    ``render.nodes._cluster_value_glyph``) is just another ``Glyph`` in
    ``fields`` — it draws its OWN nested box-in-box the same way, at
    whatever rect this level maps it to. Otherwise (no geometry — an older
    ``Layout``, or a heap shape this pass couldn't decode) fields fall back
    to a vertical stack of equal-height "name: value" rows fit to the box.
    Error clusters get the mustard border (``wire_error``) and the stored
    status / code / source field order; any other cluster gets the generic
    cluster brown."""

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
    # This cluster's REAL heap geometry (see class docstring). None for a
    # cluster the heap-geometry pass couldn't decode — the equal-height-row
    # fallback below then applies to every field.
    cluster_geom: ClusterGeom | None = None

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
        cg = self.cluster_geom
        if cg is not None and cg.width > 0 and cg.height > 0:
            geom_names = {f.name for f in cg.fields}
            if all(name in geom_names for name, _ in self.fields):
                self._draw_real_geometry(backend, bounds, theme, border)
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

    def _draw_real_geometry(
        self, backend: Backend, bounds: Rect, theme: Theme, border: str
    ) -> None:
        """Draw each field at its OWN real heap rect, fit into ``bounds`` by
        a single uniform scale (``cluster_geom``'s fields are relative to its
        own (0, 0) origin at its NATIVE size — see ``ClusterGeom``). Scale is
        1.0 whenever ``bounds`` already IS the cluster's real box (a plain or
        nested cluster constant); it does real work only when ``bounds`` is
        an externally-assigned cell of a different size (an array-of-
        clusters element, drawn at the array's fixed real per-row cell). A
        field's name draws at its real label rect (skipped when the heap has
        the caption hidden, i.e. ``label_rect is None``)."""
        cg = self.cluster_geom
        assert cg is not None  # only called when draw() already checked this
        bx1, by1, bx2, by2 = bounds
        scale = min((bx2 - bx1) / cg.width, (by2 - by1) / cg.height)
        geom_by_name = {f.name: f for f in cg.fields}
        label_size = 7.0
        for name, field_glyph in self.fields:
            geom = geom_by_name[name]
            vx1, vy1, vx2, vy2 = geom.value_rect
            abs_value = (
                bx1 + vx1 * scale,
                by1 + vy1 * scale,
                bx1 + vx2 * scale,
                by1 + vy2 * scale,
            )
            if abs_value[2] > abs_value[0] and abs_value[3] > abs_value[1]:
                field_glyph.draw(backend, abs_value, theme)
            if geom.label_rect is not None:
                lx1, ly1, lx2, ly2 = geom.label_rect
                abs_label = (
                    bx1 + lx1 * scale,
                    by1 + ly1 * scale,
                    bx1 + lx2 * scale,
                    by1 + ly2 * scale,
                )
                if abs_label[2] > abs_label[0] and abs_label[3] > abs_label[1]:
                    backend.text(
                        abs_label[0] + 1.0,
                        (abs_label[1] + abs_label[3]) / 2 + label_size * 0.34,
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
