"""Progressive partial->full upgrade: a leaf-loaded VI must still get its own
dependencies when later loaded in its own right.

Regression for an order-dependent call-edge bug: in a directory walk a VI that
is a SubVI of an earlier-sorted sibling was leaf-loaded (child NONE) and marked
loaded, so its own top-level load early-returned and its call edges were never
built. `run.vi` (loaded ~#26 of 31, a SubVI of an earlier sibling) came out of
the whole-repo index with `calls == []` while a single-VI load found its 18
callees. The fix tracks each VI's dependency-load depth and upgrades instead of
early-returning.
"""
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph, LoadMode
from lvkit.index.build import build_index
from lvkit.index.project import resolve_project

pytestmark = pytest.mark.needs_samples

_TESTCASE = (
    Path(__file__).resolve().parent.parent
    / ".lvkit" / "cache" / "samples"
    / "JKI-VI-Tester" / "source" / "Classes" / "TestCase"
)


def _run_out_degree(graph: InMemoryVIGraph, run_path: Path) -> int:
    for name in graph.list_vis():
        src = graph.get_vi_source_path(name)
        if src and src.resolve() == run_path and name in graph._dep_graph:
            return graph._dep_graph.out_degree(name)
    raise AssertionError("run.vi not found in graph")


def test_whole_repo_index_keeps_run_callees():
    """The user-facing symptom: run.vi's callees survive a whole-repo build."""
    if not _TESTCASE.exists():
        pytest.skip("sample class absent")
    root, vi_paths = resolve_project(_TESTCASE)
    facts = {f.name: f for f in build_index(root, vi_paths).facts}
    assert facts["run.vi"].calls, "run.vi lost its callees in the whole-repo build"
    # sanity: it calls its own class's methods
    assert any("TestCase.lvclass:" in c for c in facts["run.vi"].calls)


def test_call_edges_are_load_order_independent():
    """run.vi's call-edge count must not depend on when it's loaded."""
    if not _TESTCASE.exists():
        pytest.skip("sample class absent")
    root, vi_paths = resolve_project(_TESTCASE)
    run_path = next(p for p in vi_paths if p.name == "run.vi").resolve()

    after = InMemoryVIGraph()  # run.vi leaf-loaded before its own turn
    after.load_directory(root, LoadMode.MINIMAL, search_paths=[root])

    first = InMemoryVIGraph()  # run.vi loaded as primary first
    first.load_vi(run_path, LoadMode.MINIMAL, search_paths=[root])
    first.load_directory(root, LoadMode.MINIMAL, search_paths=[root])

    n_after = _run_out_degree(after, run_path)
    n_first = _run_out_degree(first, run_path)
    assert n_after == n_first > 0, f"order-dependent: after={n_after} first={n_first}"
