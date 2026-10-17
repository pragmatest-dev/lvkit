"""A repeat save of unchanged facts writes nothing (#131).

Every ``describe``/``render`` warms the project index with the facts of the VIs it
loaded. Re-saving facts the index already holds must leave ``index.db`` and its
WAL files exactly as they were, while a fuller load still upgrades a row and a
partial load still never downgrades one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lvkit import cache_paths
from lvkit.index.model import ClassFact, TerminalFact, VIFacts
from lvkit.index.store import db_path, load, save


def _facts(path: Path, **kw: object) -> VIFacts:
    base: dict[str, object] = {
        "path": str(path),
        "name": path.name,
        "content_sha": "abc",
        "terminals": [TerminalFact("x", "input", False, False, None)],
    }
    base.update(kw)
    return VIFacts(**base)  # type: ignore[arg-type]


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    db = db_path(root)
    return {
        p.name: (p.stat().st_mtime_ns, p.stat().st_size)
        for p in sorted(db.parent.iterdir())
    }


def test_repeat_save_leaves_db_and_wal_files_untouched(tmp_path: Path) -> None:
    vi = tmp_path / "A.vi"
    save(tmp_path, [_facts(vi)])
    before = _snapshot(tmp_path)
    save(tmp_path, [_facts(vi)])
    assert _snapshot(tmp_path) == before
    assert not db_path(tmp_path).with_name("index.db-wal").exists()
    assert not db_path(tmp_path).with_name("index.db-shm").exists()


def test_partial_load_of_same_bytes_writes_nothing(tmp_path: Path) -> None:
    vi = tmp_path / "A.vi"
    full = _facts(
        vi,
        library="C.lvclass",
        class_fact=ClassFact(owning_class="C.lvclass", parent="B.lvclass"),
    )
    save(tmp_path, [full])
    before = _snapshot(tmp_path)
    save(tmp_path, [_facts(vi)])  # no library, no class facts
    assert _snapshot(tmp_path) == before
    [row] = load(tmp_path)
    assert row.library == "C.lvclass"
    assert row.class_fact is not None and row.class_fact.parent == "B.lvclass"


def test_fuller_load_upgrades_a_partial_row(tmp_path: Path) -> None:
    vi = tmp_path / "A.vi"
    save(tmp_path, [_facts(vi)])
    before = _snapshot(tmp_path)
    save(
        tmp_path,
        [_facts(vi, library="C.lvclass", class_fact=ClassFact(owning_class="C"))],
    )
    assert _snapshot(tmp_path) != before
    [row] = load(tmp_path)
    assert row.library == "C.lvclass"
    assert row.class_fact is not None and row.class_fact.owning_class == "C"


def test_a_stub_never_replaces_a_loaded_vi(tmp_path: Path) -> None:
    vi = tmp_path / "A.vi"
    save(tmp_path, [_facts(vi)])
    save(tmp_path, [_facts(vi, is_stub=True, terminals=[])])
    [row] = load(tmp_path)
    assert not row.is_stub
    assert [t.name for t in row.terminals] == ["x"]


def test_changed_bytes_are_trusted_fully(tmp_path: Path) -> None:
    vi = tmp_path / "A.vi"
    save(tmp_path, [_facts(vi, library="C.lvclass")])
    save(tmp_path, [_facts(vi, content_sha="def")])
    [row] = load(tmp_path)
    assert row.content_sha == "def"
    assert row.library is None


def test_whole_graph_scores_survive_a_same_bytes_resave(tmp_path: Path) -> None:
    vi = tmp_path / "A.vi"
    save(tmp_path, [_facts(vi, impact_score=7, callers_count=3)])
    save(tmp_path, [_facts(vi, library="C.lvclass")])
    [row] = load(tmp_path)
    assert (row.impact_score, row.callers_count) == (7, 3)


_SAMPLE = Path(
    ".lvkit/cache/samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi"
)


@pytest.mark.needs_samples
def test_second_describe_writes_nothing_under_the_cache(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    if not _SAMPLE.exists():
        pytest.skip(f"sample VI not available: {_SAMPLE}")
    from lvkit.cli import main

    def run() -> None:
        monkeypatch.setattr(sys, "argv", ["lvkit", "describe", str(_SAMPLE)])
        assert main() == 0
        capsys.readouterr()

    def snapshot() -> dict[str, tuple[int, int]]:
        root = cache_paths.global_cache_root()
        return {
            str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in root.rglob("*")
            if p.is_file()
        }

    run()
    first = snapshot()
    assert any("index.db" in name for name in first)
    run()
    assert snapshot() == first
