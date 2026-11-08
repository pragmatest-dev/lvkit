"""Each run's own library folders decide where its VIs' cache slots go.

The folders live on the run (``InMemoryVIGraph.cache_roots``) and are passed to
``cache_paths.classify``; runs sharing a process (MCP tool calls run in threads of
one process) never see each other's folders. No corpus is read: ``classify`` is
path arithmetic over empty ``.vi`` files.
"""

from __future__ import annotations

import threading
from pathlib import Path

from lvkit import cache_paths
from lvkit.cache_paths import LibraryRoots
from lvkit.graph import InMemoryVIGraph


def _vi(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def _cache() -> Path:
    return cache_paths.global_cache_root()


def test_a_vi_under_the_runs_userlib_is_filed_as_shared(tmp_path: Path) -> None:
    userlib = tmp_path / "userlib"
    vi = _vi(userlib / "a" / "X.vi")
    roots = LibraryRoots.of(userlib_root=userlib)

    target, source, ns = cache_paths.classify(vi, "extract", roots)

    slug = cache_paths._slug(userlib.resolve())
    xfp = cache_paths.kind_fingerprint("extract")
    vslot = cache_paths._vi_slot(Path("a") / "X.vi")
    assert target == _cache() / "shared" / "userlib" / slug / "extract" / xfp / vslot
    assert source == str(Path("a") / "X.vi")
    assert ns == "shared"


def test_without_library_folders_the_same_vi_is_filed_under_its_project(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    vi = _vi(tmp_path / "userlib" / "a" / "X.vi")

    target, source, ns = cache_paths.classify(vi, "extract", LibraryRoots())

    slug = cache_paths._slug(tmp_path.resolve())
    xfp = cache_paths.kind_fingerprint("extract")
    vslot = cache_paths._vi_slot(Path("userlib") / "a" / "X.vi")
    assert target == _cache() / "projects" / slug / "extract" / xfp / vslot
    assert source == str(Path("userlib") / "a" / "X.vi")
    assert ns == "projects"


def test_without_library_folders_or_a_project_the_vi_is_adhoc(tmp_path: Path) -> None:
    vi = _vi(tmp_path / "userlib" / "a" / "X.vi")

    target, source, ns = cache_paths.classify(vi, "extract", LibraryRoots())

    xfp = cache_paths.kind_fingerprint("extract")
    assert target == _cache() / "adhoc" / "extract" / xfp / cache_paths._vi_slot(
        vi.resolve()
    )
    assert source == "X.vi"
    assert ns == "adhoc"


def test_a_graph_keeps_its_library_folders_when_another_graph_is_built(
    tmp_path: Path,
) -> None:
    vi = _vi(tmp_path / "userlib" / "a" / "X.vi")
    graph_a = InMemoryVIGraph()
    graph_a.set_library_roots(userlib_root=tmp_path / "userlib")
    graph_b = InMemoryVIGraph()
    graph_b.set_library_roots()

    assert cache_paths.classify(vi, "extract", graph_a.cache_roots)[2] == "shared"
    assert cache_paths.classify(vi, "extract", graph_b.cache_roots)[2] == "adhoc"


def test_a_graph_without_library_folders_has_none() -> None:
    assert InMemoryVIGraph().cache_roots == LibraryRoots()


def test_graphs_in_threads_each_classify_by_their_own_library_folders(
    tmp_path: Path,
) -> None:
    rounds = 200
    vis = [_vi(tmp_path / f"lib{i}" / "user.lib" / "X.vi") for i in range(2)]
    barrier = threading.Barrier(2)
    answers: list[list[tuple[Path, str]]] = [[], []]

    def run(i: int) -> None:
        graph = InMemoryVIGraph()
        graph.set_library_roots(userlib_root=tmp_path / f"lib{i}" / "user.lib")
        barrier.wait()
        for _ in range(rounds):
            target, _, ns = cache_paths.classify(vis[i], "extract", graph.cache_roots)
            answers[i].append((target, ns))
            barrier.wait()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()

    for i in range(2):
        own = tmp_path / f"lib{i}" / "user.lib"
        expected = _cache() / "shared" / "userlib" / cache_paths._slug(own.resolve())
        assert len(answers[i]) == rounds
        assert all(
            ns == "shared" and expected in target.parents for target, ns in answers[i]
        )
