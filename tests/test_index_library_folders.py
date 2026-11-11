"""The index is built with the run's library folders: a dependency recorded as
``<userlib>/…`` resolves through them, each VI whose facts depend on them records
them, and a sync with other folders rebuilds only those VIs."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from lvkit import cli
from lvkit.build_options import BuildOptions
from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.index import store
from lvkit.index.build import SyncResult, sync_index, warm_all_loaded
from lvkit.index.model import VIFacts
from lvkit.index.project import resolve_project_files
from lvkit.load_mode import LoadMode
from lvkit.mcp import server as srv
from tests.test_call_identity import _static, _Tree


class _Project(_Tree):
    """A project with a ``caller.vi`` that records its call to ``X.vi`` as
    ``<userlib>/Sub/X.vi``; ``userlib`` is the folder that holds ``Sub/X.vi``."""

    userlib: Path


def _make(root: Path, userlib: Path, monkeypatch: pytest.MonkeyPatch) -> _Project:
    built = _Project(root)
    built.userlib = userlib
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr("lvkit.structure.extract_vi_xml", lambda _p: (None, None, None))
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    built.vi(
        "caller.vi", "caller.vi", [], [_static(["X.vi"], ["<userlib>", "Sub", "X.vi"])]
    )
    return built


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Project:
    """The user.lib folder is inside the project, so a VI under it is also a
    project VI (found through the top VI's folder on the default search path,
    whether or not the folder is given)."""
    built = _make(tmp_path / "proj", tmp_path / "proj" / "ulib", monkeypatch)
    built.vi("ulib/Sub/X.vi", "X.vi", [])
    return built


@pytest.fixture
def outside(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Project:
    """The user.lib folder is beside the project: only the folder finds X.vi."""
    built = _make(tmp_path / "proj", tmp_path / "ulib", monkeypatch)
    lib = _Tree(tmp_path / "ulib")
    lib.vi("Sub/X.vi", "X.vi", [])
    built._xml.update(lib._xml)
    return built


def _build(project: _Project, *, userlib: bool) -> BuildOptions:
    return BuildOptions(userlib_root=(project.userlib,) if userlib else ())


def _sync(project: _Project, build: BuildOptions | None) -> SyncResult:
    root, vi_paths, ctl_paths = resolve_project_files(project.root)
    return sync_index(root, vi_paths, ctl_paths, build=build)


def _caller(facts: list[VIFacts]) -> VIFacts:
    (caller,) = [f for f in facts if Path(f.path).name == "caller.vi"]
    return caller


def _callee_keys(facts: list[VIFacts]) -> list[str | None]:
    return [n.callee_key for n in _caller(facts).nodes if n.qualified_name]


def _stored_libraries(project: _Project) -> dict[str, str | None]:
    """``{VI file name: the folders its stored row records}``."""
    return {Path(f.path).name: f.libraries for f in store.load(project.root)}


def _stored_digests(project: _Project) -> dict[str, str]:
    conn = store._connect(project.root)
    try:
        rows = conn.execute("SELECT vi_path, facts_digest FROM meta").fetchall()
    finally:
        conn.close()
    return {Path(path).name: digest for path, digest in rows}


def _rebuilt(done: SyncResult) -> set[str]:
    assert done.refresh is not None
    return {Path(p).name for p in done.refresh.rebuilt}


def test_the_user_lib_folder_resolves_the_dependency_in_the_index(
    outside: _Project,
) -> None:
    resolved = _sync(outside, _build(outside, userlib=True))
    assert _callee_keys(resolved.facts) == [
        str((outside.userlib / "Sub/X.vi").resolve())
    ]


def test_without_the_folder_the_dependency_is_unresolved(outside: _Project) -> None:
    assert _callee_keys(_sync(outside, None).facts) == [None]


def test_a_folder_dependent_vi_records_its_folders(outside: _Project) -> None:
    with_folder = _build(outside, userlib=True)
    assert _sync(outside, with_folder).refresh is None
    assert _stored_libraries(outside) == {"caller.vi": with_folder.library_key()}
    assert _rebuilt(_sync(outside, with_folder)) == set()


def test_other_folders_rebuild_the_vi_that_calls_into_them(outside: _Project) -> None:
    a, b = _build(outside, userlib=True), _build(outside, userlib=False)
    assert _sync(outside, a).refresh is None
    second = _sync(outside, b)
    assert _rebuilt(second) == {"caller.vi"}
    assert _stored_libraries(outside) == {"caller.vi": b.library_key()}
    assert _callee_keys(second.facts) == [None]
    assert _rebuilt(_sync(outside, b)) == set()


class _Mixed(_Tree):
    """Three VIs: ``plain.vi`` calls ``dep/D.vi`` at its recorded path, ``lib.vi``
    calls ``<userlib>/Sub/X.vi`` and ``missing.vi`` calls ``nope/M.vi``, which does
    not exist. ``ulib_a`` and ``ulib_b`` hold ``Sub/X.vi``; ``ulib_c`` does not."""

    ulib_a: Path
    ulib_b: Path
    ulib_c: Path


@pytest.fixture
def mixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Mixed:
    built = _Mixed(tmp_path / "proj")
    built.ulib_a, built.ulib_b = tmp_path / "ulib_a", tmp_path / "ulib_b"
    built.ulib_c = tmp_path / "ulib_c"
    built.ulib_c.mkdir()
    for lib in (built.ulib_a, built.ulib_b):
        tree = _Tree(lib)
        tree.vi("Sub/X.vi", "X.vi", [])
        built._xml.update(tree._xml)
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr("lvkit.structure.extract_vi_xml", lambda _p: (None, None, None))
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    built.vi("dep/D.vi", "D.vi", [])
    built.vi("plain.vi", "plain.vi", [], [_static(["D.vi"], ["", "dep", "D.vi"])])
    built.vi("lib.vi", "lib.vi", [], [_static(["X.vi"], ["<userlib>", "Sub", "X.vi"])])
    built.vi("missing.vi", "missing.vi", [], [_static(["M.vi"], ["", "nope", "M.vi"])])
    return built


def _folders(*userlib: Path) -> BuildOptions:
    return BuildOptions(userlib_root=userlib)


def _sync_mixed(project: _Mixed, build: BuildOptions) -> SyncResult:
    root, vi_paths, ctl_paths = resolve_project_files(project.root)
    return sync_index(root, vi_paths, ctl_paths, build=build)


def _facts_of(done: SyncResult, name: str) -> VIFacts:
    (found,) = [f for f in done.facts if Path(f.path).name == name]
    return found


def _lib_callee_keys(done: SyncResult) -> list[str | None]:
    nodes = _facts_of(done, "lib.vi").nodes
    return [n.callee_key for n in nodes if n.qualified_name]


def test_other_folders_rebuild_only_the_vis_they_can_affect(mixed: _Mixed) -> None:
    a, b = _folders(mixed.ulib_a), _folders(mixed.ulib_b)
    assert _sync_mixed(mixed, a).refresh is None
    digests = _stored_digests(mixed)
    second = _sync_mixed(mixed, b)
    assert _rebuilt(second) == {"lib.vi", "missing.vi"}
    after = _stored_digests(mixed)
    assert after["plain.vi"] == digests["plain.vi"]
    assert after["D.vi"] == digests["D.vi"]
    assert _stored_libraries(mixed) == {
        "D.vi": None,
        "plain.vi": None,
        "lib.vi": b.library_key(),
        "missing.vi": b.library_key(),
    }
    assert _rebuilt(_sync_mixed(mixed, b)) == set()


def test_the_facts_of_a_folder_dependent_vi_are_the_runs(mixed: _Mixed) -> None:
    _sync_mixed(mixed, _folders(mixed.ulib_a))
    in_b = _sync_mixed(mixed, _folders(mixed.ulib_b))
    assert _lib_callee_keys(in_b) == [str((mixed.ulib_b / "Sub/X.vi").resolve())]
    in_c = _sync_mixed(mixed, _folders(mixed.ulib_c))
    assert _rebuilt(in_c) == {"lib.vi", "missing.vi"}
    missing = (mixed.ulib_c / "Sub/X.vi").resolve()
    assert not missing.exists()
    assert _lib_callee_keys(in_c) == [str(missing)]


def _graph(project: _Project, build: BuildOptions) -> InMemoryVIGraph:
    graph = InMemoryVIGraph()
    graph.set_library_roots(userlib_root=build.userlib_root)
    assert graph.load_vi(project.root / "caller.vi", LoadMode.MINIMAL)
    return graph


def _mixed_graph(project: _Mixed, build: BuildOptions) -> InMemoryVIGraph:
    graph = InMemoryVIGraph()
    graph.set_library_roots(userlib_root=build.userlib_root)
    assert graph.load_vi(project.root / "lib.vi", LoadMode.MINIMAL)
    return graph


def test_warming_with_other_folders_replaces_only_the_warmed_vis(
    mixed: _Mixed,
) -> None:
    a, b = _folders(mixed.ulib_a), _folders(mixed.ulib_b)
    _sync_mixed(mixed, a)
    digests = _stored_digests(mixed)
    warm_all_loaded(_mixed_graph(mixed, b))
    after = _stored_digests(mixed)
    assert set(after) == {"D.vi", "plain.vi", "lib.vi", "missing.vi"}
    for name in ("D.vi", "plain.vi", "missing.vi"):
        assert after[name] == digests[name]
    assert after["lib.vi"] != digests["lib.vi"]
    assert _stored_libraries(mixed) == {
        "D.vi": None,
        "plain.vi": None,
        "lib.vi": b.library_key(),
        "missing.vi": a.library_key(),
    }
    assert _rebuilt(_sync_mixed(mixed, b)) == {"missing.vi"}


def test_warming_with_the_same_folders_keeps_the_other_facts(project: _Project) -> None:
    with_folder = _build(project, userlib=True)
    _sync(project, with_folder)
    warm_all_loaded(_graph(project, with_folder))
    assert _stored_libraries(project) == {
        "caller.vi": with_folder.library_key(),
        "X.vi": None,
    }


def _lvkit(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["lvkit", *argv])
    return cli.main()


def test_callers_takes_the_userlib_folder_and_prints_library_paths(
    project: _Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    flags = ["--userlib", str(project.root / "ulib"), "--no-auto-vilib"]
    root = str(project.root)
    assert _lvkit(monkeypatch, "callers", "<userlib>/Sub/X.vi", root, *flags) == 0
    assert capsys.readouterr().out.splitlines() == ["caller.vi"]
    assert _lvkit(monkeypatch, "callees", "caller.vi", root, *flags) == 0
    assert capsys.readouterr().out.splitlines() == ["<userlib>/Sub/X.vi"]


def test_callees_without_the_folder_prints_the_project_path(
    project: _Project,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = _lvkit(
        monkeypatch, "callees", "caller.vi", str(project.root), "--no-auto-vilib"
    )
    assert code == 0
    assert capsys.readouterr().out.splitlines() == ["ulib/Sub/X.vi"]


_KEYS = "SELECT callee_key FROM node WHERE kind = 'vi'"


def test_the_index_tool_takes_the_userlib_folder(outside: _Project) -> None:
    root = str(outside.root)
    done = asyncio.run(srv.index(project=root, userlib=str(outside.userlib)))
    assert done["vis"] == 1
    found = asyncio.run(srv.query(_KEYS, project=root, userlib=str(outside.userlib)))
    assert found["rows"] == [[str((outside.userlib / "Sub/X.vi").resolve())]]
    without = asyncio.run(srv.query(_KEYS, project=root, no_auto_vilib=True))
    assert without["rows"] == [[None]]
