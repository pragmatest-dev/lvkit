"""A node with no decoded label is told by its missing label, not by comparing its
name with the node type's generic name."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import ConstantNode
from tests import vi_reference_diagrams as d

_UNLABELLED_STATIC_REF = (
    '<SL__arrayElement class="statVIRef" uid="10"><termList elements="0"/>'
    "</SL__arrayElement>"
)


def _constants(graph: InMemoryVIGraph, vi: str) -> list[ConstantNode]:
    return [n for n in graph.iter_nodes(vi) if isinstance(n, ConstantNode)]


def test_static_vi_reference_without_label_is_skipped(tmp_path: Path) -> None:
    loaded = d.load(tmp_path, [_UNLABELLED_STATIC_REF], [])
    assert _constants(loaded.graph, loaded.caller) == []


def test_static_vi_reference_named_like_its_node_type_is_kept(tmp_path: Path) -> None:
    node = d.static_vi_reference(10, "Static VI Reference")
    loaded = d.load(tmp_path, [node], [])
    assert [c.value for c in _constants(loaded.graph, loaded.caller)] == [
        "Static VI Reference"
    ]
