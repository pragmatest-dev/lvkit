"""``RefnumDataTypeGlyph`` — a data-typed refnum constant (queue / notifier /
user event / …) drawn COMPACT: its own descriptive box plus a small
type-mnemonic BADGE in the corner showing its REGISTERED payload type.

LabVIEW draws these refnums compact regardless of how complex the payload
type is — verified against reference renders of a User Event control and a
Queue control, both a small box with a compact type badge in the corner,
never the payload's expanded field values. This corrects an earlier render
that composed a data-typed refnum's payload as a full nested cluster box
(issue #45's refnum trigger).
"""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import Glyph


@dataclass(frozen=True)
class RefnumDataTypeGlyph:
    """Wraps ``base`` (the refnum's own descriptive box — e.g.
    ``ConstantGlyph(lv_type_label(lv_type), color, fit=True)``) with a small
    color-bordered type-mnemonic badge in the bottom-right corner.

    ``badge_text`` is the payload's scalar mnemonic (``style.type_repr``,
    e.g. ``"abc"`` for a string queue, ``"DBL"`` for a numeric one) — empty
    for a cluster payload, which has no single-token mnemonic (the same
    convention ``type_repr`` already uses for a plain cluster terminal): the
    badge then draws as an empty box, its ``badge_color`` (the payload's own
    wire color) the only cue."""

    base: Glyph
    badge_text: str
    badge_color: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        self.base.draw(backend, bounds, theme)
        x1, y1, x2, y2 = bounds
        bw = min(x2 - x1 - 4.0, max(16.0, (x2 - x1) * 0.42))
        bh = min(y2 - y1 - 4.0, max(11.0, (y2 - y1) * 0.26))
        if bw <= 2.0 or bh <= 2.0:
            return  # box too small for a legible badge — the base box alone
        bx2, by2 = x2 - 2.0, y2 - 2.0
        bx1, by1 = bx2 - bw, by2 - bh
        backend.rect(
            bx1, by1, bx2, by2,
            fill=theme.const_fill,
            stroke=self.badge_color,
            stroke_width=1.0,
        )
        if self.badge_text:
            size = min(6.0, bh - 2.0)
            if size > 0:
                backend.text(
                    (bx1 + bx2) / 2,
                    (by1 + by2) / 2 + size * 0.35,
                    self.badge_text,
                    size,
                    fill=self.badge_color,
                )
