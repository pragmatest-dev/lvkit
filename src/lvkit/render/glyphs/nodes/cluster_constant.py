from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import Glyph


@dataclass(frozen=True)
class ClusterConstantGlyph:
    """A cluster constant drawn by COMPOSING each field's own constant glyph
    (boolean / numeric / string / …) inside a cluster box.

    Fields are laid out here as a vertical stack — each row a small field-name
    label beside that field's value glyph. (The heap DOES carry the typedef's
    front-panel element arrangement, but in oversized .ctl-editor coords;
    ``scene._compact_cluster_const_geom`` shrinks the node box to one natural
    row per field so this per-row split lands compact instead of stretching to
    the ~1031px typedef column. Honoring the true horizontal/hand-placed
    arrangements is a follow-up.) Error clusters get the mustard border
    (``wire_error``) and the stored status / code / source field order; any
    other cluster gets the generic cluster brown."""

    fields: tuple[tuple[str, Glyph], ...]
    is_error: bool = False
    fill_attr: str = "const_fill"
    # ``name: value`` per field, for a hover tooltip — useful when the cluster
    # is drawn small/collapsed and the inline values aren't legible.
    value_summary: str = ""

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
        border = theme.wire_error if self.is_error else theme.wire_cluster
        backend.rect(
            x1,
            y1,
            x2,
            y2,
            fill=getattr(theme, self.fill_attr),
            stroke=border,
            stroke_width=1.5,
        )
        if not self.fields:
            # No resolved fields (or a genuinely empty cluster): the generic
            # cluster icon, never a raw value repr.
            self._draw_generic_icon(backend, bounds, theme)
            return
        pad = 3.0
        label_size = 7.0
        row_h = (y2 - y1 - 2 * pad) / len(self.fields)
        if row_h < self._MIN_ROW_H or (x2 - x1 - 2 * pad) < self._MIN_FIELD_W:
            # Icon size: too small for a labeled/value grid, so draw LabVIEW's
            # generic cluster-constant ICON — the shell already drawn above, plus
            # a couple of small element squares (as the standard shared icon
            # shows), NOT a garbled stack of the real field values. Names on hover.
            self._draw_generic_icon(backend, bounds, theme)
            return
        self._draw_labeled_rows(backend, bounds, theme, border, pad, label_size)

    def _draw_generic_icon(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        """LabVIEW's generic cluster-constant icon: the cluster shell (already
        drawn) with a couple of small element squares inside — the standard
        shared look, independent of the real field values."""
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        s = max(2.0, min(w, h) * 0.22)  # element-square size
        gap = s * 0.5
        # two element squares (a string-pink + an int-blue), the mixed-element
        # motif of the standard icon; positioned upper-left with a small margin.
        colors = (theme.wire_string, theme.wire_int)
        ex = x1 + w * 0.22
        ey = y1 + h * 0.28
        for i, col in enumerate(colors):
            bx = ex + i * (s + gap)
            backend.rect(bx, ey, bx + s, ey + s, fill=col, stroke="none")
        # a third (bool-green) square below the first, per the icon's layout
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
