"""The extraction pool extracts exactly what ``extract_vi_xml`` would, only
ahead of the loader, and never turns a failure into anything but the loader's
own error.

The tiny VIs under ``tests/corpus/issues`` are checked in, so these run without
the sample corpus. Two tests start real spawned workers; the rest stub the
executor.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
from concurrent.futures import Future
from pathlib import Path

import pytest

import lvkit.extract_pool as extract_pool
from lvkit import cache_paths
from lvkit.extract_pool import ExtractionPool
from lvkit.extractor import cached_extraction, extract_vi_xml
from lvkit.graph.core import InMemoryVIGraph

SOURCE_VIS = Path(__file__).parent / "corpus" / "issues" / "30"
VI_NAMES = ["visible-frame-case-sequence.vi", "visible-frame-conditional-disable.vi"]


@pytest.fixture
def vis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Two un-extracted VIs in a project (a ``.git`` marker) and an empty cache."""
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path / "cache"))
    src = tmp_path / "src"
    (src / ".git").mkdir(parents=True)
    for name in VI_NAMES:
        shutil.copy(SOURCE_VIS / name, src / name)
    return [src / name for name in VI_NAMES]


def _cache_listing(root: Path) -> list[tuple[str, str]]:
    """Every extracted file (relative path, content hash) under the cache's
    ``projects`` tree, metadata excluded."""
    return sorted(
        (str(p.relative_to(root)), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in (root / "projects").rglob("*")
        if p.is_file() and not p.name.endswith(".meta.json")
    )


class _FailingExecutor:
    """Every submitted extraction fails, as a crashed worker would."""

    submitted: list[str] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        type(self).submitted = []

    def submit(self, fn: object, path: str) -> Future[None]:
        type(self).submitted.append(path)
        future: Future[None] = Future()
        future.set_exception(RuntimeError("worker died"))
        return future

    def shutdown(self, **kwargs: object) -> None:
        pass


def test_pool_extracts_the_same_files_as_serial(
    vis: list[Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for vi in vis:
        extract_vi_xml(vi)
    serial = _cache_listing(tmp_path / "cache")

    # A second, empty cache: the first holds this process's staging lock open,
    # which Windows won't let a test delete.
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path / "pool-cache"))
    with ExtractionPool() as pool:
        pool.prefetch(vis)
        for vi in vis:
            pool.wait(vi)
        assert pool._executor is not None
    assert all(cached_extraction(vi) is not None for vi in vis)
    assert _cache_listing(tmp_path / "pool-cache") == serial


def test_cached_extraction_matches_extract_vi_xml(vis: list[Path]) -> None:
    vi = vis[0]
    assert cached_extraction(vi) is None
    extracted = extract_vi_xml(vi)
    assert cached_extraction(vi) == extracted
    vi.write_bytes(vi.read_bytes() + b"\0")  # content changed: a miss again
    assert cached_extraction(vi) is None


def test_extracted_files_start_no_pool(vis: list[Path]) -> None:
    for vi in vis:
        extract_vi_xml(vi)
    with ExtractionPool() as pool:
        pool.prefetch(vis)
        assert pool._executor is None
        assert pool._futures == {}


def test_sibling_vis_outside_any_project_keep_their_own_extraction(
    vis: list[Path],
) -> None:
    """Outside a project each VI has its own slot: extracting one sibling does
    not invalidate the other."""
    shutil.rmtree(vis[0].parent / ".git")
    for vi in vis:
        extract_vi_xml(vi)
    assert all(cached_extraction(vi) is not None for vi in vis)


def test_missing_file_starts_no_pool(tmp_path: Path) -> None:
    with ExtractionPool() as pool:
        pool.prefetch([tmp_path / "absent.vi"])
        assert pool._executor is None


def test_prefetch_submits_each_file_once(
    vis: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_pool, "ProcessPoolExecutor", _FailingExecutor)
    with ExtractionPool() as pool:
        pool.prefetch(vis)
        pool.prefetch(vis)
    assert len(_FailingExecutor.submitted) == len(vis)


def test_off_main_thread_starts_no_pool(
    vis: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_pool, "ProcessPoolExecutor", _FailingExecutor)
    _FailingExecutor.submitted = []
    seen: dict[str, object] = {}

    def run() -> None:
        with ExtractionPool() as pool:
            pool.prefetch(vis)
            pool.wait(vis[0])
            seen["executor"] = pool._executor

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert seen["executor"] is None
    assert _FailingExecutor.submitted == []


def test_worker_failure_falls_back_to_the_loaders_own_extraction(
    vis: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract_pool, "ProcessPoolExecutor", _FailingExecutor)
    graph = InMemoryVIGraph()
    with ExtractionPool() as pool:
        graph._extract_pool = pool
        pool.prefetch(vis)
        # wait() swallows the worker's failure; the graph's own extraction runs
        assert graph._extract(vis[0]) == extract_vi_xml(vis[0])


def test_failure_on_an_unreadable_vi_raises_the_loaders_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path / "cache"))
    bad = tmp_path / "bad.vi"
    bad.write_bytes(b"not a vi")
    with pytest.raises(RuntimeError) as direct:
        extract_vi_xml(bad)
    monkeypatch.setattr(extract_pool, "ProcessPoolExecutor", _FailingExecutor)
    graph = InMemoryVIGraph()
    with ExtractionPool() as pool:
        graph._extract_pool = pool
        pool.prefetch([bad])
        with pytest.raises(RuntimeError) as pooled:
            graph._extract(bad)
    assert str(pooled.value) == str(direct.value)


def test_workers_adopt_the_parents_library_roots(tmp_path: Path) -> None:
    try:
        extract_pool._init_worker(
            (tmp_path / "vilib",),
            (tmp_path / "userlib",),
            (tmp_path / "instrlib",),
        )
        assert cache_paths.extraction_roots() == (
            ((tmp_path / "vilib").resolve(),),
            ((tmp_path / "userlib").resolve(),),
            ((tmp_path / "instrlib").resolve(),),
        )
    finally:
        cache_paths.clear_extraction_roots()


def test_outermost_load_owns_the_pool_and_nested_loads_reuse_it(
    vis: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = InMemoryVIGraph()
    seen: list[ExtractionPool | None] = []
    monkeypatch.setattr(
        InMemoryVIGraph,
        "_load_vi_recursive",
        lambda self, *a, **k: seen.append(self._extract_pool),
    )
    graph.load_vi(vis[0])
    graph.load_directory(vis[0].parent)
    assert graph._extract_pool is None
    assert len(seen) == 3  # one load_vi, then the directory's two
    assert seen[0] is not None
    assert seen[1] is seen[2] is not None
    assert seen[0] is not seen[1]


class _RecordingExecutor:
    """Records every executor started, its submissions and its shutdown calls."""

    instances: list[_RecordingExecutor] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.submitted: list[str] = []
        self.shutdowns: list[dict[str, object]] = []
        type(self).instances.append(self)

    def submit(self, fn: object, path: str) -> Future[None]:
        self.submitted.append(path)
        future: Future[None] = Future()
        future.set_result(None)
        return future

    def shutdown(self, **kwargs: object) -> None:
        self.shutdowns.append(kwargs)


def test_each_full_batch_retires_its_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(extract_pool, "ProcessPoolExecutor", _RecordingExecutor)
    monkeypatch.setattr(extract_pool, "_MAX_WORKERS", 1)
    monkeypatch.setattr(extract_pool, "_FILES_PER_WORKER", 2)
    _RecordingExecutor.instances = []
    files = []
    for i in range(7):
        vi = tmp_path / f"f{i}.vi"
        vi.write_bytes(b"x")
        files.append(vi)
    drain = {"wait": True}
    retire = {"wait": False, "cancel_futures": False}
    close = {"wait": True, "cancel_futures": True}
    with ExtractionPool() as pool:
        pool.prefetch(files[:2])
        first = _RecordingExecutor.instances
        assert len(first) == 1
        assert first[0].shutdowns == [retire]
        pool.prefetch(files[2:4])
        assert len(_RecordingExecutor.instances) == 2
        assert _RecordingExecutor.instances[1].shutdowns == [retire]
        assert first[0].shutdowns == [retire, drain]  # older one drained
        pool.prefetch(files[4:7])
        instances = _RecordingExecutor.instances
        assert len(instances) == 4
        assert [len(e.submitted) for e in instances] == [2, 2, 2, 1]
        assert instances[1].shutdowns == [retire, drain]
        assert instances[2].shutdowns == [retire]
        assert instances[3].shutdowns == []
    assert instances[3].shutdowns == [close]
    assert instances[2].shutdowns == [retire, close]
    assert instances[1].shutdowns == [retire, drain]


def test_real_pool_retiring_every_batch_extracts_everything(
    vis: list[Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for vi in vis:
        extract_vi_xml(vi)
    serial = _cache_listing(tmp_path / "cache")

    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path / "pool-cache"))
    monkeypatch.setattr(extract_pool, "_MAX_WORKERS", 1)
    monkeypatch.setattr(extract_pool, "_FILES_PER_WORKER", 1)
    with ExtractionPool() as pool:
        for vi in vis:
            pool.prefetch([vi])
        for vi in vis:
            pool.wait(vi)
    assert all(cached_extraction(vi) is not None for vi in vis)
    assert _cache_listing(tmp_path / "pool-cache") == serial
