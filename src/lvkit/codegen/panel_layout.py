"""The ``layout`` of a generated ``Panel``: where the objects of the front panel
are, as the file stores them.

``Panel(..., layout={...})`` is written from the panel's object tree
(``ParsedFrontPanel.layout``, ``parser/fp_structure.py``) and answers Panes[],
Decorations[] and All Objects[] of the panel, Controls[] and Origin of a pane,
Decos[] and AllObjs[] of a cluster, and the Position and Bounds of every control and
decoration (``panel_objects_module.py`` holds the classes that read it). The
literal is plain tuples:

- ``"panes"``: one tuple of items for each pane of the panel's hierarchy, in
  splitter order (the first side before the second);
- ``"clusters"``: the name of each cluster control to its spec,
  ``(items, specs)``: the items of the cluster's own pane and, for each field in
  cluster order, the spec of that field when it is a cluster (else None).

An item is, in stored order (first = topmost), ``("c", name, top, left, bottom,
right)`` for a control or indicator (by its name in the panel), ``("f", position,
top, left, bottom, right)`` for the field of a cluster (its position in cluster
order), or ``("d", top, left, bottom, right, hidden)`` for a decoration (a free
decoration or a free label). A rectangle is the stored one in the space of the
pane that holds the object, which is where NI's Position is measured ("relative to
the origin of the owning pane", ``generic/gobject/left.html``): the children of a
group or of a tab control's pages are stored relative to the container
(``children_in_container_space``) and are moved by its top-left.

A free label is a decoration (NI's class hierarchy lists Text under Decoration,
``class-hierarchy.html``, and its Text property under ``decoration/text``). An
object the panel gives no name (a Subpanel, a control with no terminal) is left
out.
"""

from __future__ import annotations

import ast
from typing import Any

from lvkit.parser.models import (
    ParsedFPControl,
    ParsedFPDecoration,
    ParsedFPGrouper,
    ParsedFPLabel,
    ParsedFPObject,
    ParsedFPPane,
    ParsedFPSplitter,
    ParsedFrontPanel,
)

from .ast_utils import parse_expr

Rect = tuple[int, int, int, int]


def _moved(bounds: Rect, by: tuple[int, int]) -> Rect:
    top, left, bottom, right = bounds
    return (top + by[0], left + by[1], bottom + by[0], right + by[1])


def _decoration(obj: ParsedFPDecoration | ParsedFPLabel, by: tuple[int, int]) -> Any:
    return ("d", *_moved(obj.bounds, by), obj.hidden)


class _Items:
    """The items of one pane or cluster, from its stored objects."""

    def __init__(self, names: dict[str, str], in_container: bool) -> None:
        self._names = names
        self._in_container = in_container
        self.items: list[Any] = []

    def add(self, objects: list[ParsedFPObject], by: tuple[int, int]) -> None:
        for obj in objects:
            if isinstance(obj, (ParsedFPDecoration, ParsedFPLabel)):
                self.items.append(_decoration(obj, by))
            elif isinstance(obj, ParsedFPGrouper):
                self.add(obj.objects, self._inside(obj.bounds, by))
            elif isinstance(obj, ParsedFPControl):
                name = self._names.get(obj.uid)
                if name is not None:
                    self.items.append(("c", name, *_moved(obj.bounds, by)))
                inside = self._inside(obj.bounds, by)
                for page in obj.tab_pages:
                    self.add(page.objects, inside)

    def _inside(self, container: Rect, by: tuple[int, int]) -> tuple[int, int]:
        """Where a container's children start, in the space of the pane."""
        if not self._in_container:
            return by
        return (container[0] + by[0], container[1] + by[1])


def _leaf_panes(node: ParsedFPPane | ParsedFPSplitter) -> list[ParsedFPPane]:
    if isinstance(node, ParsedFPSplitter):
        return [*_leaf_panes(node.first), *_leaf_panes(node.second)]
    return [node]


def _cluster_spec(cluster: ParsedFPControl) -> Any:
    """The spec of the cluster control ``cluster``; None when its pane lists no
    object or its fields cannot be matched to its objects."""
    if not cluster.cluster_objects:
        return None
    position = {id(child): i for i, child in enumerate(cluster.children)}
    items: list[Any] = []
    for obj in cluster.cluster_objects:
        if isinstance(obj, (ParsedFPDecoration, ParsedFPLabel)):
            items.append(_decoration(obj, (0, 0)))
        elif isinstance(obj, ParsedFPControl) and id(obj) in position:
            items.append(("f", position[id(obj)], *obj.bounds))
    specs = tuple(_cluster_spec(child) for child in cluster.children)
    return (tuple(items), specs)


def _clusters(
    objects: list[ParsedFPObject], names: dict[str, str], found: dict[str, Any]
) -> None:
    for obj in objects:
        if isinstance(obj, ParsedFPGrouper):
            _clusters(obj.objects, names, found)
        elif isinstance(obj, ParsedFPControl):
            name = names.get(obj.uid)
            if name is not None and (spec := _cluster_spec(obj)) is not None:
                found[name] = spec
            for page in obj.tab_pages:
                _clusters(page.objects, names, found)


def layout_expression(
    front_panel: ParsedFrontPanel | None, names: dict[str, str]
) -> ast.expr | None:
    """The ``layout`` keyword's value for the panel ``front_panel`` (``names``: the
    fPDCO uid of each control and indicator to its name in the panel); None when
    the file records no object tree."""
    if front_panel is None or front_panel.layout is None:
        return None
    panes: list[Any] = []
    clusters: dict[str, Any] = {}
    for pane in _leaf_panes(front_panel.layout):
        items = _Items(names, front_panel.children_in_container_space)
        items.add(pane.objects, (0, 0))
        panes.append(tuple(items.items))
        _clusters(pane.objects, names, clusters)
    return parse_expr(repr({"panes": tuple(panes), "clusters": clusters}))
