"""``FeedbackNodeGlyph`` — a LabVIEW Feedback Node (master/slave pair; see
``parser.node_types.FeedbackNode``): ONE small wire-type-colored box drawn at
the master's (``hiddenFBNode``) position, since master and slave always share
identical ``<bounds>`` in the heap -- see ``render.nodes._feedback_node_glyph``
for how both sides' terminal data feeds this single glyph.

Verified against Raph's real #107 screenshots (both Feedback Nodes in
FPGA_v1.vi): the icon is a small box holding a bold arrow with a full
shaft+head (never a bare triangle) pointing toward wherever the node's
output actually flows (``arrow_left``, decoded from the master's own
``leftFeedback`` dco ``<termBMPs>`` code -- see
``parser.node_types.FeedbackNode.output_bmps``). A SECOND, shorter cell
stacks below the arrow ONLY when the master's heap recorded an initializer
terminal at all (``init_cell`` is ``None`` otherwise, e.g. the "T"-latched
Feedback Node in FPGA_v1.vi, which has no initializer terminal and draws as
a single bare arrow cell): a small asterisk/sparkle when that terminal is
unwired (NI docs: defaults to the type's own default value; confirmed on
FPGA_v1.vi's own "Data FIFO Full" latch), or a small upward merge arrow when
it has a real incoming wire (no corpus example of this in FPGA_v1.vi; shape
follows the issue's own reference icon).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ....parser.layout import Rect
from ...backend import Backend
from ...style import Theme

InitCell = Literal["default", "wired"] | None


@dataclass(frozen=True)
class FeedbackNodeGlyph:
    """``color`` is the feedback value's own wire-type color (``None`` falls
    back to ``theme.sr_stroke``). ``arrow_left`` picks which way the main
    cell's arrow points. ``init_cell`` is ``None`` (single cell, no second
    row at all), ``"default"`` (unwired initializer -- asterisk marker), or
    ``"wired"`` (wired initializer -- small merge arrow)."""

    color: str | None = None
    arrow_left: bool = True
    init_cell: InitCell = None

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        col = self.color or theme.sr_stroke
        if self.init_cell is None:
            arrow_y1, arrow_y2 = y1, y2
        else:
            arrow_y2 = (y1 + y2) / 2
            arrow_y1 = y1
            backend.rect(
                x1, arrow_y2, x2, y2, fill=theme.loop_term_fill, stroke=col,
                stroke_width=1.0,
            )
            if self.init_cell == "default":
                self._draw_default_marker(backend, x1, arrow_y2, x2, y2, col)
            else:
                self._draw_arrow(backend, x1, arrow_y2, x2, y2, col, "up")
        backend.rect(
            x1, arrow_y1, x2, arrow_y2, fill=theme.loop_term_fill, stroke=col,
            stroke_width=1.2,
        )
        self._draw_arrow(
            backend, x1, arrow_y1, x2, arrow_y2, col,
            "left" if self.arrow_left else "right",
        )

    @staticmethod
    def _draw_arrow(
        backend: Backend,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        color: str,
        direction: Literal["left", "right", "up"],
    ) -> None:
        """A bold arrow with a full shaft + triangular head (never a bare
        chevron) -- matches the real LabVIEW icon's weight/style."""
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        w, h = x2 - x1, y2 - y1
        if direction == "up":
            # Same shape rotated 90 degrees, scaled to the shorter cell.
            head_h = h * 0.6
            shaft_half = w * 0.14
            head_half = w * 0.32
            points = [
                (cx - shaft_half, y2 - h * 0.1),
                (cx - shaft_half, y1 + head_h),
                (cx - head_half, y1 + head_h),
                (cx, y1 + h * 0.08),
                (cx + head_half, y1 + head_h),
                (cx + shaft_half, y1 + head_h),
                (cx + shaft_half, y2 - h * 0.1),
            ]
            backend.polygon(points, fill=color)
            return
        head_w = w * 0.5
        shaft_half = h * 0.16
        head_half = h * 0.34
        tail_x = x2 - w * 0.1
        head_base_x = x1 + head_w
        tip_x = x1 + w * 0.08
        points = [
            (tail_x, cy - shaft_half),
            (head_base_x, cy - shaft_half),
            (head_base_x, cy - head_half),
            (tip_x, cy),
            (head_base_x, cy + head_half),
            (head_base_x, cy + shaft_half),
            (tail_x, cy + shaft_half),
        ]
        if direction == "right":
            points = [(x1 + x2 - px, py) for px, py in points]
        backend.polygon(points, fill=color)

    @staticmethod
    def _draw_default_marker(
        backend: Backend, x1: float, y1: float, x2: float, y2: float, color: str
    ) -> None:
        """An 8-point asterisk/sparkle ("✳") in the default-value cell --
        matching NI's own docs reference image for this marker -- a
        clean-room REDRAWING of that shape, never NI's own icon artwork.
        4 spokes alone reads as a plus sign, not an asterisk."""
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        r = min(x2 - x1, y2 - y1) * 0.3
        rd = r * 0.70710678  # diagonal spoke length (r / sqrt(2))
        for dx, dy in (
            (r, 0.0),
            (-r, 0.0),
            (0.0, r),
            (0.0, -r),
            (rd, rd),
            (rd, -rd),
            (-rd, rd),
            (-rd, -rd),
        ):
            backend.line(cx, cy, cx + dx, cy + dy, stroke=color, stroke_width=1.0)
