"""Tests for class (LVOOP) wire style (issue #43): decoding a class's
``CoreWirePen``/``EdgeWirePen`` into a ``WireStyle`` (structure.py::
_parse_wire_style), resolving it through the graph with ancestry inheritance
(core.py::get_class_wire_style), stamping it onto the class-instance type
(_enrich_type), and — via the one wire lookup (render.style.wire_style) — drawing
a class wire in the class's own color instead of the generic default.

Sample-backed; skipped when the corpus isn't present, matching the other
sample-backed tests (e.g. test_class_parent_linkinfo.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvkit.cli import _auto_search_paths
from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.models import WireLineStyle
from lvkit.render import render_vi
from lvkit.structure import parse_lvclass

AF_ROOT = Path(".lvkit/cache/samples/actor-framework/Core/ActorFramework")
JKI_ROOT = Path(".lvkit/cache/samples/JKI-VI-Tester/source")
CAF_ROOT = Path(".lvkit/cache/samples/configurable-af-example/source")

ACTOR_CLASS = AF_ROOT / "Actor" / "Actor.lvclass"
ACTOR_METHOD = AF_ROOT / "Actor" / "Handle Error.vi"
TESTLOADER_CLASS = JKI_ROOT / "Classes" / "TestLoader" / "TestLoader.lvclass"
CONFIG_ACTOR_CLASS = CAF_ROOT / "ConfigurableActor" / "ConfigurableActor.lvclass"


pytestmark = pytest.mark.skipif(
    not ACTOR_CLASS.exists(), reason="actor-framework sample not present"
)


def test_class_pen_decodes_to_its_wire_style() -> None:
    """Actor.lvclass sets a custom pen — its wire is the Actor-Framework blue,
    total width 3, solid."""
    cls = parse_lvclass(ACTOR_CLASS)
    assert cls.wire_style is not None
    assert cls.wire_style.color == "#006CFF"
    assert cls.wire_style.width == 3
    assert cls.wire_style.line_style == WireLineStyle.SOLID


@pytest.mark.skipif(not TESTLOADER_CLASS.exists(), reason="JKI sample not present")
def test_each_class_decodes_its_own_color() -> None:
    """A different class decodes a different color — the style is data-driven,
    not a shared constant."""
    cls = parse_lvclass(TESTLOADER_CLASS)
    assert cls.wire_style is not None
    assert cls.wire_style.color == "#94A27C"


@pytest.mark.skipif(not CONFIG_ACTOR_CLASS.exists(), reason="CAF sample not present")
def test_no_pen_is_none() -> None:
    """A class that never customized its wire has no pen properties → None
    (it uses the default/inherited look)."""
    cls = parse_lvclass(CONFIG_ACTOR_CLASS)
    assert cls.wire_style is None


def _load(vi_path: Path) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    sp = _auto_search_paths([], vi_path, vi_path)
    graph.load_vi(str(vi_path), LoadMode.MINIMAL, search_paths=sp, layout=True)
    return graph, graph.resolve_vi_name(vi_path.name)


def test_graph_resolves_qualified_classname() -> None:
    """get_class_wire_style resolves the library-qualified classname a wire
    actually carries (``Actor Framework.lvlib:Actor.lvclass``) to the class's
    own style."""
    graph, vi_name = _load(ACTOR_METHOD)
    style = graph.get_class_wire_style("Actor Framework.lvlib:Actor.lvclass", vi_name)
    assert style is not None
    assert style.color == "#006CFF"


def test_type_carries_its_wire_style() -> None:
    """The class-instance type is given its wire style at enrichment, so the one
    lookup reads it off the type with no graph."""
    from lvkit.render.style import wire_style

    graph, vi_name = _load(ACTOR_METHOD)
    ctx = graph.get_vi_context(vi_name)
    class_terms = [
        t
        for t in (*ctx.inputs, *ctx.outputs)
        if t.lv_type is not None and t.lv_type.classname
    ]
    assert class_terms, "no Actor class terminal found"
    for t in class_terms:
        assert t.lv_type is not None and t.lv_type.wire_style is not None
        assert wire_style(t.lv_type).color == "#006CFF"


def test_class_wire_rendered_in_class_color() -> None:
    """The Actor object wire on Handle Error.vi renders in the class's own blue,
    not the generic default wire color."""
    graph, vi_name = _load(ACTOR_METHOD)
    svg = render_vi(graph, vi_name)
    assert svg is not None
    assert 'stroke="#006CFF"' in svg


def test_class_color_reaches_pane_and_terminal_via_the_one_lookup() -> None:
    """Because every surface goes through wire_style, the class color also shows
    on the connector-pane cell — no per-surface plumbing."""
    graph, vi_name = _load(ACTOR_METHOD)
    svg = render_vi(graph, vi_name)
    assert svg is not None
    cells = re.findall(r'lv-pane-cell[^>]*fill="([^"]+)"', svg)
    assert "#006CFF" in cells
