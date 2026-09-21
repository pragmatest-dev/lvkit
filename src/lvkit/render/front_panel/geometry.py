"""Front-panel geometry: place every TOP-LEVEL control at its real absolute
box.

A top-level control's own ``ParsedFPControl.bounds`` is already panel-absolute
(the heap stores it that way), so that's all this module places. A cluster's
or array's INTERIOR content (its fields/element, at THEIR real relative
geometry) is no longer placed here -- ``ClusterConstantGlyph``/
``ArrayConstantGlyph`` (the existing block-diagram glyphs, reused as-is via
``controls.resolve_glyph``) already solve real per-field/per-element
placement via their own ``cluster_geom``/``cell_h``/``cell_w`` uniform-scale
fit, given the bounds this module hands them for the top-level control --
placing it a SECOND time here would just be this file's own former
duplicate of that same scale math.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...parser.layout import Rect
from ...parser.models import ParsedFPControl, ParsedFrontPanel


@dataclass(frozen=True)
class FPBox:
    """One TOP-LEVEL control, placed at its real absolute ``bounds``."""

    control: ParsedFPControl
    bounds: Rect  # (x1, y1, x2, y2) -- ABSOLUTE


def _to_rect(bounds: tuple[int, int, int, int]) -> Rect:
    """``ParsedFPControl.bounds`` is ``(top, left, bottom, right)``;
    ``layout.Rect`` is ``(x1, y1, x2, y2)`` = ``(left, top, right, bottom)``."""
    top, left, bottom, right = bounds
    return (float(left), float(top), float(right), float(bottom))


def content_bounds(boxes: list[FPBox], pad: float = 12.0) -> Rect:
    """The real bounding box over every TOP-LEVEL control's own placed
    ``bounds``, padded -- the SVG viewBox. NOT ``ParsedFrontPanel.panel_bounds``
    (LabVIEW's own front-panel WINDOW/viewport size, verified on the real
    corpus to be SMALLER than the actual control extent when the developer's
    controls extend past the visible/scrolled window -- e.g. a real .ctl
    whose panel_bounds x-range is [-234, 432] while its root control spans
    [206, 1239])."""
    if not boxes:
        return (0.0, 0.0, 100.0, 100.0)
    xs = [x for b in boxes for x in (b.bounds[0], b.bounds[2])]
    ys = [y for b in boxes for y in (b.bounds[1], b.bounds[3])]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def build_boxes(front_panel: ParsedFrontPanel) -> list[FPBox]:
    """Every top-level control on ``front_panel``, at its real absolute box."""
    return [FPBox(ctrl, _to_rect(ctrl.bounds)) for ctrl in front_panel.controls]
