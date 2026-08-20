"""Regression tests over the committed issue-reproduction corpus.

Each test renders / describes / inspects a minimal repro that a user attached to
a GitHub issue (kept under ``tests/corpus/issues/<N>/``, Apache-2.0 — see that
dir's README) and asserts the corrected behaviour, so a fixed bug stays fixed.
These fixtures are IN-REPO, so unlike the ``needs_samples`` corpus tests they
always run.
"""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.op_walk import _flatten_leaf_fields, _nmux_field_sources
from lvkit.models import LVTypeKind

_CORPUS = Path(__file__).resolve().parent / "corpus" / "issues"


def _load(rel: str) -> tuple[InMemoryVIGraph, str]:
    vi = _CORPUS / rel
    graph = InMemoryVIGraph()
    graph.load_vi(vi, mode=LoadMode.MINIMAL, search_paths=[vi.parent], layout=True)
    return graph, graph.resolve_vi_name(vi.name)


def test_issue36_bundle_unbundle_field_names_use_the_selected_field():
    """#36: a bundle/unbundle-by-name drawer that selects cluster field index 0
    has its ``<i>``/``<index>`` element OMITTED by LabVIEW; lvkit used to fall
    back to the drawer's list position, mislabeling it. The reporter's cluster is
    ``[Name, Elements in array, size]`` and the three drawers select
    ``[Elements in array, size, Name]`` — the last (index 0) rendered as a second
    "size" before the fix.
    """
    graph, vi = _load("36/bundle-unbundle-names.vi")
    checked = 0
    for node in graph.iter_nodes(vi):
        nt = str(getattr(node, "node_type", None))
        if nt not in ("nMux", "decomposeClusterNode"):
            continue
        agg = next(
            (
                t
                for t in node.terminals
                if t.lv_type is not None and t.lv_type.kind == LVTypeKind.CLUSTER
            ),
            None,
        )
        own, dep = _nmux_field_sources(vi, agg, graph)
        names = [f[1].name for f in _flatten_leaf_fields(dep or own)]
        if names != ["Name", "Elements in array", "size"]:
            continue  # waveform / digital-data aggregates (Bug B) are out of scope
        drawers = [
            names[t.nmux_field_index]
            for t in node.terminals
            if t.nmux_field_index is not None
            and 0 <= t.nmux_field_index < len(names)
        ]
        assert "Name" in drawers, f"index-0 'Name' drawer lost: {drawers}"
        assert drawers.count("size") == 1, f"drawer duplicated 'size' (#36): {drawers}"
        checked += 1
    assert checked >= 4, f"expected the custom-cluster nodes; {checked=}"
