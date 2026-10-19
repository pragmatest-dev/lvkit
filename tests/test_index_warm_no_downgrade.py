"""Regression: a partial (progressive-warm) re-save must never downgrade a
full build's container facts.

``store.save()`` is delete-then-reinsert by path. Progressive warming
(``build.warm_index_for_vi`` / ``warm_all_loaded``) persists facts from a
single-VI graph that never loaded the class hierarchy, so ``library`` and
``class_fact`` come back ``None`` even when a prior full build resolved them.
Without coalescing, that partial save clobbers the good row with NULLs.

The same holds for a SubVI call's ``callee_path``: only a full sync resolves it
(it needs the whole project), so a progressive write must keep the stored one.

Pure store-layer test — hand-built ``VIFacts``/``ClassFact``, no sample corpus,
no VI parsing.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from lvkit.index import build
from lvkit.index.model import ClassFact, NodeFact, NodeKind, VIFacts
from lvkit.index.store import load as load_index
from lvkit.index.store import save as save_index


def _facts(
    path: Path,
    *,
    content_sha: str,
    library: str | None,
    class_fact: ClassFact | None,
) -> VIFacts:
    return VIFacts(
        path=str(path),
        name=path.name,
        content_sha=content_sha,
        library=library,
        class_fact=class_fact,
    )


def test_same_sha_partial_resave_preserves_container_facts(tmp_path: Path):
    """A same-content partial re-save (library=None, class_fact=None) must not
    clobber a prior full build's library/owning_class/parent/scope."""
    vi_path = tmp_path / "Method.vi"

    full = _facts(
        vi_path,
        content_sha="abc",
        library="MyClass.lvclass",
        class_fact=ClassFact(
            owning_class="MyClass.lvclass",
            parent="Base.lvclass",
            scope="public",
        ),
    )
    save_index(tmp_path, [full])

    partial = _facts(
        vi_path,
        content_sha="abc",
        library=None,
        class_fact=None,
    )
    save_index(tmp_path, [partial])

    [loaded] = [f for f in load_index(tmp_path) if f.path == str(vi_path)]
    assert loaded.library == "MyClass.lvclass"
    assert loaded.class_fact is not None
    assert loaded.class_fact.owning_class == "MyClass.lvclass"
    assert loaded.class_fact.parent == "Base.lvclass"
    assert loaded.class_fact.scope == "public"


def test_changed_sha_resave_clears_container_facts(tmp_path: Path):
    """A DIFFERENT content_sha means the VI actually changed — the incoming
    (NULL) facts are trusted fully, clearing the stale prior row."""
    vi_path = tmp_path / "Method.vi"

    full = _facts(
        vi_path,
        content_sha="abc",
        library="MyClass.lvclass",
        class_fact=ClassFact(
            owning_class="MyClass.lvclass",
            parent="Base.lvclass",
            scope="public",
        ),
    )
    save_index(tmp_path, [full])

    changed = _facts(
        vi_path,
        content_sha="def",
        library=None,
        class_fact=None,
    )
    save_index(tmp_path, [changed])

    [loaded] = [f for f in load_index(tmp_path) if f.path == str(vi_path)]
    assert loaded.content_sha == "def"
    assert loaded.library is None
    assert loaded.class_fact is None


# --- callee_path: resolved across the whole project, never by a single load ---


def _caller(path: Path, callee: Path, callee_path: str | None) -> VIFacts:
    return VIFacts(
        path=str(path),
        name=path.name,
        content_sha="abc",
        nodes=[
            NodeFact(
                uid="10",
                kind=NodeKind.VI,
                qualified_name=callee.name,
                callee_path=callee_path,
            )
        ],
    )


def _loaded_callee_paths(root: Path, path: Path) -> list[str | None]:
    [loaded] = [f for f in load_index(root) if f.path == str(path)]
    return [n.callee_path for n in loaded.nodes]


class _OneVIGraph:
    """Just enough of a loaded graph for ``warm_all_loaded``: one VI on disk and
    no controls."""

    def __init__(self, vi_path: Path) -> None:
        self.vi_path = vi_path
        self.typedefs_listed = False

    def list_vis(self) -> list[str]:
        return [self.vi_path.name]

    def get_vi_source_path(self, vi_name: str) -> Path:
        return self.vi_path

    def list_typedefs(self) -> list[str]:
        self.typedefs_listed = True
        return []


def _warm_all_loaded(graph: _OneVIGraph) -> None:
    build.warm_all_loaded(graph)  # type: ignore[arg-type]


def _warm_index_for_vi(graph: _OneVIGraph) -> None:
    build.warm_index_for_vi(graph, graph.vi_path.name, graph.vi_path)  # type: ignore[arg-type]


@pytest.mark.parametrize("warm", [_warm_all_loaded, _warm_index_for_vi])
def test_progressive_write_keeps_a_resolved_callee_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warm: Callable[[_OneVIGraph], None],
):
    """A full sync resolves a SubVI call to its callee's path. Loading the
    unchanged caller again (describe, render, read_vi) writes its facts with
    the callee not yet resolved; the stored callee path must survive."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    caller = root / "Caller.vi"
    callee = root / "Callee.vi"
    caller.write_bytes(b"caller")
    callee.write_bytes(b"callee")
    save_index(root, [_caller(caller, callee, str(callee.resolve()))])

    projected: list[str] = []

    def _project(graph, vi_name, vi_path, project_root) -> VIFacts:
        projected.append(vi_name)
        return _caller(caller, callee, None)

    monkeypatch.setattr(build, "project_vi_facts", _project)
    graph = _OneVIGraph(caller)
    warm(graph)

    # The warm really ran (it swallows every error), and kept the callee path.
    assert projected == ["Caller.vi"] and graph.typedefs_listed
    assert _loaded_callee_paths(root, caller) == [str(callee.resolve())]


def test_full_sync_can_clear_a_callee_path(tmp_path: Path):
    """A full sync's resolution is the truth even for unchanged caller bytes:
    when the callee is gone from the project, its callee path is cleared."""
    caller = tmp_path / "Caller.vi"
    callee = tmp_path / "Callee.vi"
    save_index(tmp_path, [_caller(caller, callee, str(callee))])

    save_index(tmp_path, [_caller(caller, callee, None)])

    assert _loaded_callee_paths(tmp_path, caller) == [None]
