"""The index is built with the run's library folders: a dependency recorded as
``<userlib>/…`` resolves through them, the saved index records them, and a write
with other folders rebuilds it."""

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


def _stored_libraries(project: _Project) -> str | None:
    conn = store._connect(project.root)
    try:
        row = conn.execute("SELECT libraries FROM index_meta WHERE id = 0").fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def test_the_user_lib_folder_resolves_the_dependency_in_the_index(
    outside: _Project,
) -> None:
    resolved = _sync(outside, _build(outside, userlib=True))
    assert _callee_keys(resolved.facts) == [
        str((outside.userlib / "Sub/X.vi").resolve())
    ]


def test_without_the_folder_the_dependency_is_unresolved(outside: _Project) -> None:
    assert _callee_keys(_sync(outside, None).facts) == [None]


def test_the_saved_index_remembers_its_folders(outside: _Project) -> None:
    with_folder = _build(outside, userlib=True)
    assert _sync(outside, with_folder).refresh is None
    assert _stored_libraries(outside) == with_folder.library_key()
    assert _sync(outside, with_folder).refresh is not None


def test_other_folders_rebuild_the_index_and_are_recorded(outside: _Project) -> None:
    a, b = _build(outside, userlib=True), _build(outside, userlib=False)
    assert _sync(outside, a).refresh is None
    second = _sync(outside, b)
    assert second.refresh is None
    assert _stored_libraries(outside) == b.library_key()
    assert _callee_keys(second.facts) == [None]
    assert _sync(outside, b).refresh is not None


def test_an_index_without_recorded_folders_is_rebuilt(outside: _Project) -> None:
    _sync(outside, None)
    conn = store._connect(outside.root)
    try:
        with conn:
            conn.execute("UPDATE index_meta SET libraries = NULL")
    finally:
        conn.close()
    assert _sync(outside, None).refresh is None


def _graph(project: _Project, build: BuildOptions) -> InMemoryVIGraph:
    graph = InMemoryVIGraph()
    graph.set_library_roots(userlib_root=build.userlib_root)
    assert graph.load_vi(project.root / "caller.vi", LoadMode.MINIMAL)
    return graph


def test_warming_with_other_folders_resets_the_index(project: _Project) -> None:
    with_folder = _build(project, userlib=True)
    _sync(project, with_folder)
    warm_all_loaded(_graph(project, _build(project, userlib=False)))
    assert _stored_libraries(project) == _build(project, userlib=False).library_key()
    names = {Path(f.path).name for f in store.load(project.root)}
    assert names == {"caller.vi"}
    assert _caller(store.load(project.root)).path == str(
        (project.root / "caller.vi").resolve()
    )


def test_warming_with_the_same_folders_keeps_the_other_facts(project: _Project) -> None:
    with_folder = _build(project, userlib=True)
    _sync(project, with_folder)
    warm_all_loaded(_graph(project, with_folder))
    assert _stored_libraries(project) == with_folder.library_key()
    names = {Path(f.path).name for f in store.load(project.root)}
    assert names == {"caller.vi", "X.vi"}


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
    without = asyncio.run(srv.query(_KEYS, project=root))
    assert without["rows"] == [[None]]
