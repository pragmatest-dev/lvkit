"""The value glyph for a ``stdGraph`` control."""

from __future__ import annotations

from dataclasses import dataclass

from ....parser.layout import Rect
from ....parser.models import ParsedFPControl
from ...backend import Backend
from ...style import Theme
from .base import part_rect

_PLOT_AREA_PART_ID = 28


@dataclass(frozen=True)
class GraphControlGlyph:
    """A frame + the plot area's own real inset (real corpus heap: a distinct
    ``partID`` 28 rect, consistently inset from the outer frame's ``partID``
    9) -- ``inset`` is that rect as left/top/right/bottom FRACTIONS of the
    control's own heap box, so it maps onto however large this glyph is
    actually drawn.

    A real graph's OTHER sibling parts this does not draw -- two ``scale``
    parts (axis tick chrome), a ``treeControl`` and a ``stdClust`` (their
    specific purpose, e.g. a legend / a cursor palette, is a plausible
    reading of the part class name and typical LabVIEW graph UI, NOT verified
    against NI's own docs -- unlike ``partID`` 9/28 above, whose role IS
    confirmed by their consistent, measured position on real controls), and
    toolbar buttons -- regularly occupy a comparable or LARGER area than the
    plot itself (measured on real corpus data: one sample's plot area is
    31.6% of the control while its largest other sibling alone is 62.9%).
    Plot data (traces, axis scale values) is not decoded here either --
    LabVIEW's own scale/plot-data heap format for a graph is a much larger
    undertaking than a control's shape, out of scope for this pass -- so this
    never fakes a trace or invents axis numbers, only the plot area's own
    real boundary, drawn over the rest of the control as plain panel fill."""

    border_color: str
    inset: tuple[float, float, float, float] | None  # left, top, right, bottom

    @classmethod
    def from_control(
        cls, ctrl: ParsedFPControl, border_color: str
    ) -> GraphControlGlyph:
        return cls(border_color, _plot_inset(ctrl))

    def draw(self, backend: Backend, bounds: Rect, theme: Theme) -> None:
        x1, y1, x2, y2 = bounds
        backend.rect(
            x1, y1, x2, y2,
            fill=theme.fp_value_fill, stroke=self.border_color, stroke_width=1.0,
        )
        if self.inset is None:
            return
        left, top, right, bottom = self.inset
        w, h = x2 - x1, y2 - y1
        # theme.canvas equals theme.fp_value_fill by default, so the inset
        # reads only via its own stroke, not a fill contrast -- deliberate
        # (an outline of the real boundary, not a filled "plot" that would
        # look like real content).
        backend.rect(
            x1 + w * left, y1 + h * top, x2 - w * right, y2 - h * bottom,
            fill=theme.canvas, stroke=self.border_color, stroke_width=1.0,
        )


def _plot_inset(ctrl: ParsedFPControl) -> tuple[float, float, float, float] | None:
    """The plot area's real recorded rect, as left/top/right/bottom fractions
    of the control's own heap box. ``None`` when the heap carries no such
    part (an older/simpler graph variant), or when the recorded rect leaves
    no room to draw (an inverted/overflowing part -- other sibling parts in
    this heap format are routinely recorded past a control's own box, though
    no real corpus plot-area part has been seen doing so yet)."""
    plot = part_rect(ctrl, _PLOT_AREA_PART_ID)
    if plot is None:
        return None
    top, left, bottom, right = ctrl.bounds
    width, height = float(right - left), float(bottom - top)
    if width <= 0 or height <= 0:
        return None
    px1, py1, px2, py2 = plot
    left_f = max(0.0, min(1.0, px1 / width))
    top_f = max(0.0, min(1.0, py1 / height))
    right_f = max(0.0, min(1.0, (width - px2) / width))
    bottom_f = max(0.0, min(1.0, (height - py2) / height))
    if left_f + right_f >= 1.0 or top_f + bottom_f >= 1.0:
        return None
    return (left_f, top_f, right_f, bottom_f)
