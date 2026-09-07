"""``ClassGlyph`` — a LVOOP class-typed field or constant (``LVType.
classname`` set — verified heap ddo class ``udClassDDO``): a clean-room CUBE
mark (LabVIEW draws a class as a cube; ours is our own outline, never traced
NI artwork) plus the class's own SHORT name (``style.lv_type_label`` — lib
qualifier stripped, ``.lvclass`` kept) below it, replacing the bare
"LabVIEW Object" / class-name TEXT box a class field used to draw (issue
#45's class-field bug — LabVIEW is a visual language, not "a box with spaced
words")."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import fit_label

# Below this box size there's no room for a border + cube + name legibly —
# LabVIEW-style MINI form: just the cube, scaled up, no chrome (see
# refnum_glyph._MINI_MAX_W/_MINI_MAX_H for the same convention).
_MINI_MAX = 28.0


@dataclass(frozen=True)
class ClassGlyph:
    """``name`` is the class's short display name; ``color`` is the class's
    own wire color (``style.wire_style`` — the class's decoded pen, or the
    grey-chain default for an unresolved class)."""

    name: str
    color: str

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        if w < _MINI_MAX or h < _MINI_MAX:
            # MINI form: just the cube, filling the box — no border, no
            # name (there's no room to show either legibly).
            pad = 1.5
            cube = min(w, h) - 2 * pad
            if cube > 3.0:
                self._draw_cube(backend, x1 + pad, y1 + pad, cube, self.color)
            return
        backend.rect(
            x1, y1, x2, y2,
            fill=theme.const_fill,
            stroke=self.color,
            stroke_width=1.2,
        )
        pad = 3.0
        cube = min(w - 2 * pad, h - 2 * pad, max(0.0, h * 0.55))
        text_y = y2 - pad
        if cube > 6.0:
            self._draw_cube(backend, x1 + pad, y1 + pad, cube, self.color)
            text_y = min(y1 + pad + cube + 8.0, y2 - pad)
        if self.name:
            size = min(7.0, max(4.0, h * 0.24))
            label = fit_label(self.name, w - 2 * pad, backend, size)
            if label:
                backend.text(
                    (x1 + x2) / 2, text_y, label, size, fill=self.color,
                )

    def _draw_cube(
        self, backend: Backend, x: float, y: float, s: float, color: str
    ) -> None:
        """An isometric cube outline (front/top/side faces as three
        parallelograms sharing edges) — the classic "class = box" motif, drawn
        as strokes only (no fill) so it stays legible on either theme without
        a new palette token."""
        depth = s * 0.32
        front = s - depth
        # Front face (bottom-left square-ish face).
        fx1, fy1 = x, y + depth
        fx2, fy2 = x + front, y + depth + front
        backend.polygon(
            [(fx1, fy1), (fx2, fy1), (fx2, fy2), (fx1, fy2)],
            fill="none", stroke=color, stroke_width=1.0,
        )
        # Top face (parallelogram receding up-right).
        backend.polygon(
            [
                (fx1, fy1), (x + depth, y),
                (x + depth + front, y), (fx2, fy1),
            ],
            fill="none", stroke=color, stroke_width=1.0,
        )
        # Side face (parallelogram receding up-right, right of the front).
        backend.polygon(
            [
                (fx2, fy1), (x + depth + front, y),
                (x + depth + front, y + front), (fx2, fy2),
            ],
            fill="none", stroke=color, stroke_width=1.0,
        )
