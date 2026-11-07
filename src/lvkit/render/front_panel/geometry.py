"""Front-panel placement: every object of the panel's tree at its box in the
saved window's coordinates.

The parser keeps every position as stored (``ParsedFrontPanel.layout``); this
module is the one place that turns them into window positions:

* a plain pane's object sits at its stored position shifted by the pane's scroll
  ``origin``; the saved window is the rect ``(0, 0, pane width, pane height)``
  (the pane's own top-left is the window's place on the screen, not part of the
  mapping);
* the root splitter's top-left is its screen position too, so its local space
  starts at ``(0, 0)``; each child's ``bounds`` is in its parent splitter's local
  space, and a leaf pane's objects are clipped to the pane;
* a grouper's children, and the shown page of a tab control, are stored relative
  to the container's top-left, except in a VI saved before LabVIEW 9.0, where
  they are stored in the space of the pane that holds the container;
* a pane's own colour fills the canvas when it is the panel's first pane, and
  its own rect when it is a pane of a splitter;
* every object list is stored front first, so it is placed last to first.

A cluster's or array's INTERIOR (its fields / element) is placed by the resolved
glyph itself (``ClusterConstantGlyph`` and ``ArrayControlGlyph``, via
``controls.resolve_glyph``), which maps its own heap-local geometry onto the box
it is handed.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...parser.layout import Rect
from ...parser.models import (
    ParsedFPControl,
    ParsedFPDecoration,
    ParsedFPGrouper,
    ParsedFPLabel,
    ParsedFPObject,
    ParsedFPPane,
    ParsedFPSplitter,
    ParsedFrontPanel,
)
from .controls.base import stored_rect
from .controls.tab import tab_row_rect

# Panels saved before this LabVIEW major version store a container's children in
# the space of the pane that holds the container.
_CONTAINER_LOCAL_FROM_MAJOR = 9

# Thickness of the band drawn between the two panes of a splitter.
_BAR_WIDTH = 2.0

Offset = tuple[float, float]


@dataclass(frozen=True)
class FPWindowFrame:
    """The saved window's edge."""


@dataclass(frozen=True)
class FPSplitterBar:
    """The band between the two panes of a splitter."""


@dataclass(frozen=True)
class FPPaneBackground:
    """The fill of a pane of a splitter."""

    color: str


FPPlaced = (
    ParsedFPControl
    | ParsedFPLabel
    | ParsedFPDecoration
    | FPWindowFrame
    | FPSplitterBar
    | FPPaneBackground
)


@dataclass(frozen=True)
class FPBox:
    """One placed object: ``bounds`` is its rect in window coordinates and
    ``clip`` the rect it is clipped to (None when unclipped). ``extent`` is the
    rect it paints in when that reaches outside ``bounds`` (a tab control's tab
    row), None otherwise. A list of boxes is in drawing order, first drawn
    first."""

    obj: FPPlaced
    bounds: Rect  # (x1, y1, x2, y2)
    clip: Rect | None = None
    extent: Rect | None = None


def _shifted(bounds: tuple[int, int, int, int], shift: Offset) -> Rect:
    x1, y1, x2, y2 = stored_rect(bounds)
    return (x1 + shift[0], y1 + shift[1], x2 + shift[0], y2 + shift[1])


def _intersect(a: Rect, b: Rect) -> Rect | None:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    return (x1, y1, x2, y2) if x1 <= x2 and y1 <= y2 else None


def _tab_extent(ctrl: ParsedFPControl, rect: Rect) -> Rect | None:
    """The rect a tab control paints in: its box and its tab row, which can lie
    outside the box. None for any other control."""
    row = tab_row_rect(ctrl) if ctrl.control_type == "tabControl" else None
    if row is None:
        return None
    return (
        min(rect[0], rect[0] + row[0]),
        min(rect[1], rect[1] + row[1]),
        max(rect[2], rect[0] + row[2]),
        max(rect[3], rect[1] + row[3]),
    )


def _container_local(front_panel: ParsedFrontPanel) -> bool:
    """Whether a container's children are stored relative to the container: from
    LabVIEW 9.0, and when the file records no version."""
    version = front_panel.lv_version
    if version is None:
        return True
    major = version.split(".")[0]
    return not major.isdigit() or int(major) >= _CONTAINER_LOCAL_FROM_MAJOR


class _Placer:
    def __init__(self, container_local: bool) -> None:
        self._container_local = container_local
        self.boxes: list[FPBox] = []

    def place_node(
        self, node: ParsedFPPane | ParsedFPSplitter, offset: Offset, clipped: bool
    ) -> None:
        """Place a pane or splitter whose local space starts at ``offset`` in
        window coordinates. ``clipped`` is True for a pane of a splitter."""
        if isinstance(node, ParsedFPSplitter):
            self._place_splitter(node, offset)
            return
        top, left, bottom, right = node.bounds
        pane_rect: Rect = (
            offset[0],
            offset[1],
            offset[0] + (right - left),
            offset[1] + (bottom - top),
        )
        if clipped and node.background is not None:
            self.boxes.append(FPBox(FPPaneBackground(node.background), pane_rect))
        shift = (offset[0] + node.origin[0], offset[1] + node.origin[1])
        self._place_objects(node.objects, shift, pane_rect if clipped else None)

    def _place_splitter(self, node: ParsedFPSplitter, offset: Offset) -> None:
        for child in (node.first, node.second):
            child_top, child_left = child.bounds[0], child.bounds[1]
            self.place_node(
                child, (offset[0] + child_left, offset[1] + child_top), clipped=True
            )
        self._place_bar(node, offset)

    def _place_bar(self, node: ParsedFPSplitter, offset: Offset) -> None:
        first = _shifted(node.first.bounds, offset)
        second = _shifted(node.second.bounds, offset)
        if node.side_by_side:
            x = (first[2] + second[0]) / 2
            bar: Rect = (
                x - _BAR_WIDTH / 2,
                min(first[1], second[1]),
                x + _BAR_WIDTH / 2,
                max(first[3], second[3]),
            )
        else:
            y = (first[3] + second[1]) / 2
            bar = (
                min(first[0], second[0]),
                y - _BAR_WIDTH / 2,
                max(first[2], second[2]),
                y + _BAR_WIDTH / 2,
            )
        self.boxes.append(FPBox(FPSplitterBar(), bar))

    def _place_objects(
        self,
        objects: list[ParsedFPObject],
        shift: Offset,
        clip: Rect | None,
    ) -> None:
        """``shift`` turns the objects' stored positions into window positions."""
        for obj in reversed(objects):
            if isinstance(obj, (ParsedFPLabel, ParsedFPControl)) and obj.hidden:
                continue
            rect = _shifted(obj.bounds, shift)
            if isinstance(obj, ParsedFPGrouper):
                self._place_objects(obj.objects, self._inner_shift(rect, shift), clip)
                continue
            if isinstance(obj, ParsedFPControl):
                self.boxes.append(FPBox(obj, rect, clip, _tab_extent(obj, rect)))
                if obj.tab_pages:
                    self._place_tab_page(obj, rect, shift, clip)
            else:
                self.boxes.append(FPBox(obj, rect, clip))

    def _place_tab_page(
        self, tab: ParsedFPControl, rect: Rect, shift: Offset, clip: Rect | None
    ) -> None:
        shown = tab.tab_selected if tab.tab_selected is not None else 0
        if not 0 <= shown < len(tab.tab_pages):
            return
        page = tab.tab_pages[shown]
        self._place_objects(page.objects, self._inner_shift(rect, shift), clip)

    def _inner_shift(self, container: Rect, outer: Offset) -> Offset:
        if self._container_local:
            return (container[0], container[1])
        return outer


def panel_background(front_panel: ParsedFrontPanel) -> str | None:
    """The colour of the panel's first pane (the top-left leaf of its splitters),
    None when it records none."""
    node = front_panel.layout
    while isinstance(node, ParsedFPSplitter):
        node = node.first
    return node.background if node is not None else None


def _window(front_panel: ParsedFrontPanel) -> Rect | None:
    layout = front_panel.layout
    if layout is None:
        return None
    top, left, bottom, right = layout.bounds
    if right <= left or bottom <= top:
        return None
    return (0.0, 0.0, float(right - left), float(bottom - top))


def build_boxes(
    front_panel: ParsedFrontPanel, window_frame: bool = True
) -> list[FPBox]:
    """Every object of ``front_panel`` at its box in window coordinates, in
    drawing order, ending with the saved window's frame when ``window_frame``. A
    panel with no object tree draws its controls at their stored bounds."""
    layout = front_panel.layout
    if layout is None:
        return [
            FPBox(ctrl, stored_rect(ctrl.bounds))
            for ctrl in front_panel.controls
            if not ctrl.hidden
        ]
    placer = _Placer(_container_local(front_panel))
    placer.place_node(layout, (0.0, 0.0), clipped=False)
    boxes = placer.boxes
    window = _window(front_panel) if window_frame else None
    if window is not None:
        boxes.append(FPBox(FPWindowFrame(), window))
    return boxes


def content_bounds(boxes: list[FPBox], pad: float = 12.0) -> Rect:
    """The bounding box over every placed object (cut to its clip) and the window
    frame, padded -- the SVG viewBox. NOT ``ParsedFrontPanel.panel_bounds``
    (zero-sized in 7% of files, and smaller than the controls' extent when the
    panel is scrolled)."""
    rects = [
        (
            (box.extent or box.bounds)
            if box.clip is None
            else _intersect(box.extent or box.bounds, box.clip)
        )
        for box in boxes
    ]
    placed = [r for r in rects if r is not None]
    if not placed:
        return (0.0, 0.0, 100.0, 100.0)
    xs = [x for r in placed for x in (r[0], r[2])]
    ys = [y for r in placed for y in (r[1], r[3])]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
