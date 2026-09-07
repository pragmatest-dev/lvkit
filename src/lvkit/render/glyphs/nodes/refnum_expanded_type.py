"""``RefnumExpandedTypeGlyph`` — an EXPANDED data-typed refnum constant (a
User Event whose registered payload is shown inline, verified against a real
heap example, e.g. GTR's "ResultChangedRef"): the payload cluster's TYPE —
one DIMMED "name: TYPE" row per top-level field — filling the box.

LabVIEW records this as a genuine per-field state, distinct from the compact
icon+badge form (``RefnumDataTypeGlyph`` in ``refnum_data_type.py``) — see
``layout.ClusterFieldGeom.refnum_expanded`` / ``_refnum_type_display_expanded``
for how the heap's own recorded state (never a size guess) picks between the
two. Either way this is a TYPE display, never editable VALUE glyphs — a
refnum's payload only ever appears as real data elsewhere (e.g. an Event
Structure's own data node), never inside the refnum control itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import fit_label


@dataclass(frozen=True)
class RefnumExpandedTypeGlyph:
    """``fields`` is (name, type mnemonic) per top-level payload field —
    ``style.type_repr``'s scalar mnemonic (``abc``/``I32``/``TF``) or
    ``style.lv_type_label``'s ``Cluster``/``Error`` fallback for a
    nested-cluster field (never recursed further — a flat type-schema
    summary, not a real nested value box). Drawn DIMMED (``theme.
    disabled_mask``) since this shows what the payload's TYPE is, not a
    settable value; the border stays the refnum's own wire color so it
    still reads as a refnum box."""

    fields: tuple[tuple[str, str], ...]
    border_color: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=theme.const_fill,
            stroke=self.border_color,
            stroke_width=1.2,
        )
        if not self.fields:
            return
        pad = 3.0
        row_h = (y2 - y1 - 2 * pad) / len(self.fields)
        size = max(5.0, min(8.0, row_h * 0.5))
        dim = theme.disabled_mask
        avail = (x2 - x1) - 2 * pad
        gap = 4.0
        for i, (name, type_text) in enumerate(self.fields):
            ry1 = y1 + pad + i * row_h
            ry2 = ry1 + row_h
            if ry2 - ry1 < 2.0:
                break
            cy = (ry1 + ry2) / 2 + size * 0.34
            # A long field name and a long type mnemonic on the same row can
            # collide (e.g. "startTimestamp" vs "MeasureData") — split the
            # row's width between them and ellipsize each to its own budget
            # (never let one side's text run into the other's).
            type_w = min(avail * 0.45, backend.measure_text(type_text, size))
            name_w = max(0.0, avail - type_w - gap)
            backend.text(
                x1 + pad, cy, fit_label(name, name_w, backend, size),
                size, anchor="start", fill=dim,
            )
            backend.text(
                x2 - pad, cy, fit_label(type_text, type_w, backend, size),
                size, anchor="end", fill=dim,
            )
