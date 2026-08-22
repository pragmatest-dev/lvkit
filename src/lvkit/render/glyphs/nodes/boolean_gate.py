from __future__ import annotations

import math
from dataclasses import dataclass

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme
from .base import _GATE_ARC_SEGMENTS, _OPERATOR_SYMBOL_SIZE, _quad_bezier_points


@dataclass(frozen=True)
class BooleanGateGlyph:
    """The logic-gate SILHOUETTE LabVIEW's own boolean primitives use —
    clean-room curves (quadratic-Bezier-sampled; see ``_quad_bezier_points``)
    matching each gate's outline/aspect from the NI public function-reference
    images (goal #99), not traced NI pixels:

    - ``kind="and"``: a "D" — flat left (input) edge, straight top/bottom,
      a semicircular bulge on the right (the output tip).
    - ``kind="or"``/``kind="xor"``: a pointed "shield" — convex top/bottom
      curves converging to a point on the right (output), a concave scoop on
      the left (input edge). ``xor`` adds a second, detached concave arc just
      outside that left edge (stroke-only, no fill) — the extra "double
      line" XOR gates always carry.
    - ``kind="not"``: the same borderless right-pointing triangle as
      ``ArithGlyph`` — LabVIEW draws Not with no distinct gate body, just the
      triangle plus an input bubble.

    ``negated`` draws a small unfilled circle at the OUTPUT (right) tip —
    Not And / Not Or / Not Exclusive Or. ``input_bubble`` draws it at the
    INPUT (left) edge instead — Not itself. A gate uses at most one of the
    two in practice."""

    symbol: str
    kind: str = "and"  # "and" | "or" | "xor" | "not"
    negated: bool = False
    input_bubble: bool = False
    fill_attr: str = "prim_fill"
    stroke_attr: str = "prim_stroke"
    text_attr: str = "prim_text"
    bubble_fill_attr: str = "canvas"
    stroke_width: float = 1.2

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        fill = getattr(theme, self.fill_attr)
        stroke = getattr(theme, self.stroke_attr)
        text_fill = getattr(theme, self.text_attr)
        cy = (y1 + y2) / 2

        if self.kind == "not":
            backend.polygon(
                [(x1, y1), (x2, cy), (x1, y2)],
                fill=fill,
                stroke=stroke,
                stroke_width=self.stroke_width,
            )
            in_x, out_x = x1, x2
            sym_x = x1 + (x2 - x1) * 0.36
        elif self.kind == "and":
            out_x = self._draw_and(backend, bounds, fill, stroke)
            in_x = x1
            sym_x = x1 + (out_x - x1) * 0.42
        else:  # "or" / "xor"
            self._draw_or(backend, bounds, fill, stroke, extra_arc=self.kind == "xor")
            in_x, out_x = x1, x2
            sym_x = x1 + (x2 - x1) * 0.40

        r = max(1.4, min(3.2, min(x2 - x1, y2 - y1) * 0.11))
        bubble_fill = getattr(theme, self.bubble_fill_attr)
        if self.input_bubble:
            backend.circle(
                in_x - r,
                cy,
                r,
                fill=bubble_fill,
                stroke=stroke,
                stroke_width=1.0,
            )
        if self.negated:
            backend.circle(
                out_x + r,
                cy,
                r,
                fill=bubble_fill,
                stroke=stroke,
                stroke_width=1.0,
            )

        size = _OPERATOR_SYMBOL_SIZE
        backend.text(sym_x, cy + size * 0.34, self.symbol, size, fill=text_fill)

    @staticmethod
    def _draw_and(
        backend: Backend,
        bounds: Rect,
        fill: str,
        stroke: str,
    ) -> float:
        """Flat-left / semicircle-right "D" outline. Returns the output
        (bulge) tip's x — the right-most point, for bubble placement."""
        x1, y1, x2, y2 = bounds
        cy = (y1 + y2) / 2
        r = min((y2 - y1) / 2, (x2 - x1) * 0.5)
        x_flat = x2 - r
        # Arc from the top-flat corner (cy - r), through the rightmost bulge
        # point (x_flat + r, cy), to the bottom-flat corner (cy + r). When
        # ``r`` is height-limited (the normal, roughly-square case) this
        # meets ``y1``/``y2`` exactly, so the top/bottom flat edges below are
        # plain horizontal lines; a width-clamped ``r`` degrades gracefully
        # to a short diagonal instead of a discontinuity.
        arc = [
            (x_flat + r * math.cos(t), cy + r * math.sin(t))
            for t in (
                -math.pi / 2 + i * math.pi / _GATE_ARC_SEGMENTS
                for i in range(_GATE_ARC_SEGMENTS + 1)
            )
        ]
        pts = [(x1, y1), *arc, (x1, y2), (x1, y1)]
        backend.path(pts, stroke=stroke, stroke_width=1.2, fill=fill)
        return x2

    @staticmethod
    def _draw_or(
        backend: Backend,
        bounds: Rect,
        fill: str,
        stroke: str,
        *,
        extra_arc: bool,
    ) -> None:
        """Pointed "shield" outline: convex top/bottom curves meeting at a
        right-hand tip, concave scoop on the left. ``extra_arc`` (Exclusive
        Or) adds a second, detached concave stroke just outside the left
        edge, unfilled."""
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        cy = (y1 + y2) / 2
        tip = (x2, cy)
        top_left = (x1, y1)
        bottom_left = (x1, y2)
        top_ctrl = (x1 + w * 0.55, y1 - h * 0.08)
        bottom_ctrl = (x1 + w * 0.55, y2 + h * 0.08)
        left_ctrl = (x1 + w * 0.28, cy)

        outline = [
            *_quad_bezier_points(top_left, top_ctrl, tip),
            *_quad_bezier_points(tip, bottom_ctrl, bottom_left)[1:],
            *_quad_bezier_points(bottom_left, left_ctrl, top_left)[1:],
        ]
        backend.path(outline, stroke=stroke, stroke_width=1.2, fill=fill)

        if extra_arc:
            outset = max(2.0, w * 0.10)
            top_left2 = (x1 - outset, y1)
            bottom_left2 = (x1 - outset, y2)
            left_ctrl2 = (x1 - outset + w * 0.28, cy)
            second = _quad_bezier_points(top_left2, left_ctrl2, bottom_left2)
            backend.path(second, stroke=stroke, stroke_width=1.2, fill="none")
