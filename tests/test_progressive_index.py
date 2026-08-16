"""Progressive index population: ordinary single-VI loads warm the store.

`warm_index_for_vi` upserts one loaded VI's facts into its project index, so the
index builds up as the repo is used rather than only from a whole-repo pass. The
test copies sample VIs into an isolated project dir (no `.lvkit`/`.git` ancestor,
so the store is keyed to the temp dir, never the repo's real index) and checks
the store accumulates one row per warmed VI.
"""

import shutil
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph, LoadMode
from lvkit.index import store
from lvkit.index.build import warm_index_for_vi

pytestmark = pytest.mark.needs_samples

_TESTCASE = (
    Path(__file__).resolve().parent.parent
    / ".lvkit"
    / "cache"
    / "samples"
    / "JKI-VI-Tester"
    / "source"
    / "Classes"
    / "TestCase"
)


def _load_and_name(vi: Path, root: Path) -> tuple[InMemoryVIGraph, str]:
    g = InMemoryVIGraph()
    g.load_vi(vi, LoadMode.MINIMAL, search_paths=[root])
    for name in g.list_vis():
        src = g.get_vi_source_path(name)
        if src is not None and src.resolve() == vi.resolve():
            return g, name
    return g, g.resolve_vi_name(vi.name)


def test_warming_accumulates_rows_progressively(tmp_path):
    if not _TESTCASE.exists():
        pytest.skip("sample class absent")
    vis = sorted(_TESTCASE.glob("*.vi"))[:3]
    if len(vis) < 3:
        pytest.skip("need at least 3 sample VIs")

    proj = tmp_path / "proj"  # no .lvkit/.git ancestor -> isolated store
    proj.mkdir()

    assert store.load(proj) == []  # cold
    for i, vi in enumerate(vis, start=1):
        dst = proj / vi.name
        shutil.copy(vi, dst)
        graph, name = _load_and_name(dst, proj)
        warm_index_for_vi(graph, name, dst)
        rows = store.load(proj)
        assert len(rows) == i, f"expected {i} rows after warming {i} VIs"
        assert {r.name for r in rows} == {v.name for v in vis[:i]}


def test_warming_same_vi_twice_upserts_not_duplicates(tmp_path):
    if not _TESTCASE.exists():
        pytest.skip("sample class absent")
    vi = sorted(_TESTCASE.glob("*.vi"))[0]
    proj = tmp_path / "proj"
    proj.mkdir()
    dst = proj / vi.name
    shutil.copy(vi, dst)

    graph, name = _load_and_name(dst, proj)
    warm_index_for_vi(graph, name, dst)
    warm_index_for_vi(graph, name, dst)  # again — must upsert, not duplicate
    assert len(store.load(proj)) == 1
