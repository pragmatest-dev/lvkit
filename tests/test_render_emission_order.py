"""Emission-order pins for the R-1 render-tree fold.

Three facts about SVG emission order are byte-load-bearing — a pure refactor of
the composite tree must preserve every one of them or the corpus byte-diff moves
(and, for the clip ids and menu overlays, the JS frame controller and the id
namespace break in ways a rasterized visual diff would NOT catch). These pins
fail LOUDLY and specifically if the fold disturbs any of them, so a regression
points at the cause instead of a wall of changed bytes.

See the plan's R-1 gate (mighty-churning-unicorn.md) for the receipts.
"""

from __future__ import annotations

import re
from pathlib import Path

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.render import build_scene, render_vi_file
from lvkit.render.composite import (
    DecorationObject,
    NodeObject,
    StructureObject,
    build_render_tree,
)

_CORPUS = Path(__file__).resolve().parent / "corpus" / "issues"
_ALL_VIS = sorted(_CORPUS.rglob("*.vi"))


def _load(vi: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph.load_vi(vi, mode=LoadMode.MINIMAL, search_paths=[vi.parent], layout=True)
    return graph, graph.resolve_vi_name(vi.name)


# ---- Pin 1: clipPath ids number by draw-call ARRIVAL order -------------------

def test_pin_clip_ids_number_by_arrival_order():
    """``SvgBackend`` assigns each distinct clip rect an id ``…-cN`` where N is
    'how many distinct clips have been opened so far' (backend.py: ``setdefault(
    clip, len(self._clip_ids))``), and emits the ``<clipPath>`` defs in that same
    order. So: the first time each ``#…-cN`` is referenced (reading the document
    top-to-bottom) must be in ascending N, contiguous from 0, and the ``<defs>``
    order must match. If a structure opened its clip before painting its own
    body, or children were built/drawn in a different order, N would renumber."""
    for vi in _ALL_VIS:
        svg = render_vi_file(vi)
        assert svg is not None, vi
        refs = re.findall(r'clip-path="url\(#([^)]+)\)"', svg)
        defs = re.findall(r'<clipPath id="([^"]+)"', svg)
        if not defs:
            continue
        # First-appearance order of referenced ids == defs order.
        seen: list[str] = []
        for r in refs:
            if r not in seen:
                seen.append(r)
        assert seen == defs, f"{vi.name}: clip first-use order != defs order"
        # Suffixes are c0, c1, c2, … contiguous from 0 in that same order.
        suffixes = [d.rsplit("-c", 1)[-1] for d in defs]
        assert suffixes == [str(i) for i in range(len(defs))], (
            f"{vi.name}: clip ids not arrival-numbered from 0: {suffixes}"
        )


# ---- Pin 2: interactive-structure menu overlays emit LAST, in BUILD order ----

def test_pin_menus_emit_after_content_in_build_order():
    """Dropdown menus are an overlay pass drawn AFTER the whole composite, and in
    ``interactive_structures`` append order (the DFS build order), NOT paint
    order. The case+sequence fixture has two interactive structures; pin that
    every ``lv-menu`` group sits after the last clipped structural content and
    that their ``data-lv-struct`` order equals the builder's menu-list order."""
    vi = _CORPUS / "30" / "visible-frame-case-sequence.vi"
    graph, name = _load(vi)
    scene = build_scene(graph, name)
    assert scene is not None
    tree = build_render_tree(scene)
    build_order = [
        se.rs.node.id.rsplit("::", 1)[-1] for se in tree.interactive_structures
    ]
    assert len(build_order) >= 2, "fixture must have >=2 interactive structures"

    svg = render_vi_file(vi)
    assert svg is not None
    first_menu = svg.find('class="lv-menu"')
    last_clip = max(
        (m.start() for m in re.finditer(r'clip-path="url\(#', svg)), default=-1
    )
    assert first_menu != -1
    assert first_menu > last_clip, "menus must emit after clipped content"

    svg_menu_order = re.findall(r'<g class="lv-menu" data-lv-struct="([^"]+)"', svg)
    assert svg_menu_order == build_order, (
        f"menu emit order {svg_menu_order} != build order {build_order}"
    )


# ---- Pin 3: sibling paint order is a stable reverse sort by z-rank -----------

def test_pin_sibling_order_stable_reverse_by_rank():
    """A container's children draw back-to-front = descending z-rank (zPlaneList
    index 0 is frontmost, drawn last). The sort is STABLE and rank is -1 for any
    item with no geometry entry, so unknown-rank items tie at -1 and keep their
    append order — nodes are appended before structures. Pin: every container's
    child ranks are non-increasing, and equal-rank ties never place a structure
    before a node."""
    for vi in _ALL_VIS:
        graph, name = _load(vi)
        scene = build_scene(graph, name)
        if scene is None:
            continue
        z = scene.z_order
        tree = build_render_tree(scene)

        def rank_of(elem) -> int:
            if isinstance(elem, NodeObject):
                raw = elem.rn.dom_id
            elif isinstance(elem, DecorationObject):
                raw = elem.deco.dom_id
            else:
                raw = elem.rs.raw_uid
            return z.get(raw, -1)

        def check(content) -> None:
            ranks = [rank_of(c) for c in content.children]
            assert ranks == sorted(ranks, reverse=True), (
                f"{vi.name}: child ranks not non-increasing: {ranks}"
            )
            for a, b in zip(content.children, content.children[1:]):
                if rank_of(a) == rank_of(b):
                    assert not (
                        isinstance(a, StructureObject) and isinstance(b, NodeObject)
                    ), f"{vi.name}: structure sorted before node at equal rank"
            for c in content.children:
                if isinstance(c, StructureObject):
                    if c.body is not None:
                        check(c.body)
                    for _value, fc in c.frames:
                        check(fc)

        check(tree.content)
