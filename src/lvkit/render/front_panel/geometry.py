"""Front-panel geometry: place every control at its REAL absolute box.

A top-level control's own ``ParsedFPControl.bounds`` is already panel-absolute
(the heap stores it that way). A cluster's CHILDREN are not — their positions
live in ``ParsedFPControl.cluster_geom`` (see ``parser.layout.ClusterGeom``),
relative to the cluster's own (0, 0) at its NATIVE size. Placing a child is a
single uniform-scale fit of that native geometry into the box actually
assigned to its parent (never a per-axis stretch, matching ``ClusterGeom``'s
own documented contract) — recursing to arbitrary depth for nested clusters.

``children`` and ``cluster_geom.fields`` are NOT in the same order (the
former is cluster/``ddoList`` order, the latter is ``zPlaneList``/z-order —
verified on the real corpus) — fields are matched by NAME, never position.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...parser.layout import ClusterGeom, Rect
from ...parser.models import ParsedFPControl, ParsedFrontPanel


@dataclass(frozen=True)
class FPBox:
    """One control, placed at its real absolute ``bounds`` on the panel (or
    inside whatever box its parent cluster assigned it), with its own
    children placed the same way, recursively."""

    control: ParsedFPControl
    bounds: Rect  # (x1, y1, x2, y2) — ABSOLUTE, this control's own coord space
    label_bounds: Rect | None
    children: tuple[FPBox, ...] = field(default_factory=tuple)


def _to_rect(bounds: tuple[int, int, int, int]) -> Rect:
    """``ParsedFPControl.bounds`` is ``(top, left, bottom, right)``;
    ``layout.Rect`` is ``(x1, y1, x2, y2)`` = ``(left, top, right, bottom)``."""
    top, left, bottom, right = bounds
    return (float(left), float(top), float(right), float(bottom))


def _fit(rect: Rect, geom: ClusterGeom, box: Rect) -> Rect:
    """Map ``rect`` (in ``geom``'s native (0, 0)-origin space) into ``box`` via
    ``geom``'s own uniform-scale contract."""
    bx1, by1, bx2, by2 = box
    bw, bh = bx2 - bx1, by2 - by1
    if geom.width <= 0 or geom.height <= 0:
        return box
    s = min(bw / geom.width, bh / geom.height)
    x1, y1, x2, y2 = rect
    return (bx1 + x1 * s, by1 + y1 * s, bx1 + x2 * s, by1 + y2 * s)


def _place_children(ctrl: ParsedFPControl, box: Rect) -> tuple[FPBox, ...]:
    """Place ``ctrl``'s children (a cluster's fields, or an array-of-cluster's
    element fields) inside ``box`` using ``ctrl.cluster_geom``. Empty when
    there's no geometry to place them with (falls back to whatever the child's
    own raw bounds say, so a view still has SOMETHING to draw rather than
    dropping the control)."""
    geom = ctrl.cluster_geom
    if geom is None or not ctrl.children:
        return tuple(
            FPBox(c, _to_rect(c.bounds), None, _place_children(c, _to_rect(c.bounds)))
            for c in ctrl.children
        )
    by_name = {f.name: f for f in geom.fields}
    out = []
    for child in ctrl.children:
        f = by_name.get(child.name)
        if f is None:
            # No matching geometry for this field -- fall back to its own raw
            # bounds rather than dropping it (never silently omit a control).
            child_box = _to_rect(child.bounds)
            label_box = None
        else:
            child_box = _fit(f.value_rect, geom, box)
            label_box = _fit(f.label_rect, geom, box) if f.label_rect else None
        grandchildren = _place_children(child, child_box)
        out.append(FPBox(child, child_box, label_box, grandchildren))
    return tuple(out)


def content_bounds(boxes: list[FPBox], pad: float = 12.0) -> Rect:
    """The real bounding box over every TOP-LEVEL control's own placed
    ``bounds``, padded -- the SVG viewBox. NOT ``ParsedFrontPanel.panel_bounds``
    (LabVIEW's own front-panel WINDOW/viewport size, verified on the real
    corpus to be SMALLER than the actual control extent when the developer's
    controls extend past the visible/scrolled window -- e.g. a real .ctl
    whose panel_bounds x-range is [-234, 432] while its root control spans
    [206, 1239]). Children are always placed strictly inside their top-level
    parent's box (geometry.py's uniform-scale fit), so the top-level boxes
    alone bound everything -- mirrors ``layout.Layout.scene_bounds``'s
    identical "bounding box over every known rect, padded" contract."""
    if not boxes:
        return (0.0, 0.0, 100.0, 100.0)
    xs = [x for b in boxes for x in (b.bounds[0], b.bounds[2])]
    ys = [y for b in boxes for y in (b.bounds[1], b.bounds[3])]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def build_boxes(front_panel: ParsedFrontPanel) -> list[FPBox]:
    """Every top-level control on ``front_panel``, placed (recursively) at its
    real absolute box."""
    boxes = []
    for ctrl in front_panel.controls:
        box = _to_rect(ctrl.bounds)
        boxes.append(FPBox(ctrl, box, None, _place_children(ctrl, box)))
    return boxes
