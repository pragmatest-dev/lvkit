"""Regression: a partial (progressive-warm) re-save must never downgrade a
full build's container facts.

``store.save()`` is delete-then-reinsert by path. Progressive warming
(``build.warm_index_for_vi`` / ``warm_all_loaded``) persists facts from a
single-VI graph that never loaded the class hierarchy, so ``library`` and
``class_fact`` come back ``None`` even when a prior full build resolved them.
Without coalescing, that partial save clobbers the good row with NULLs.

Pure store-layer test — hand-built ``VIFacts``/``ClassFact``, no sample corpus,
no VI parsing.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from lvkit.graph.models import VINode
from lvkit.index import build
from lvkit.index.model import ClassFact, NodeFact, NodeKind, TypedefVersionFact, VIFacts
from lvkit.index.query import resolve_node_callee_paths
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


def _typedef_facts(
    path: Path,
    *,
    content_sha: str = "abc",
    typedefs: list[str],
    walked: bool,
) -> VIFacts:
    return VIFacts(
        path=str(path),
        name=path.name,
        content_sha=content_sha,
        typedef_paths=list(typedefs),
        typedef_versions=[TypedefVersionFact(p, f"t:{p}") for p in typedefs],
        dependencies_walked=walked,
    )


def _stored_typedefs(root: Path, vi_path: Path) -> tuple[list[str], list[str]]:
    [loaded] = [f for f in load_index(root) if f.path == str(vi_path)]
    return (
        loaded.typedef_paths,
        [v.typedef_path for v in loaded.typedef_versions],
    )


def test_shallow_load_after_deep_keeps_typedef_facts(tmp_path: Path):
    """A SubVI-only load (dependencies not walked) knows nothing about the VI's
    controls: its empty lists never replace the ones a deeper load stored."""
    vi_path = tmp_path / "Sub.vi"
    save_index(
        tmp_path, [_typedef_facts(vi_path, typedefs=["a.ctl", "b.ctl"], walked=True)]
    )
    save_index(tmp_path, [_typedef_facts(vi_path, typedefs=[], walked=False)])
    assert _stored_typedefs(tmp_path, vi_path) == (
        ["a.ctl", "b.ctl"],
        ["a.ctl", "b.ctl"],
    )


def test_deep_load_after_shallow_writes_typedef_facts(tmp_path: Path):
    vi_path = tmp_path / "Sub.vi"
    save_index(tmp_path, [_typedef_facts(vi_path, typedefs=[], walked=False)])
    save_index(
        tmp_path, [_typedef_facts(vi_path, typedefs=["a.ctl", "b.ctl"], walked=True)]
    )
    assert _stored_typedefs(tmp_path, vi_path) == (
        ["a.ctl", "b.ctl"],
        ["a.ctl", "b.ctl"],
    )


def test_deep_load_replaces_typedef_facts_when_the_list_changed(tmp_path: Path):
    """The latest load that walked the dependencies is the truth (a control the
    VI no longer reads drops out)."""
    vi_path = tmp_path / "Sub.vi"
    save_index(
        tmp_path, [_typedef_facts(vi_path, typedefs=["a.ctl", "b.ctl"], walked=True)]
    )
    save_index(tmp_path, [_typedef_facts(vi_path, typedefs=["b.ctl"], walked=True)])
    assert _stored_typedefs(tmp_path, vi_path) == (["b.ctl"], ["b.ctl"])


def test_shallow_load_of_changed_bytes_is_written_as_given(tmp_path: Path):
    vi_path = tmp_path / "Sub.vi"
    save_index(tmp_path, [_typedef_facts(vi_path, typedefs=["a.ctl"], walked=True)])
    save_index(
        tmp_path,
        [_typedef_facts(vi_path, content_sha="new", typedefs=[], walked=False)],
    )
    assert _stored_typedefs(tmp_path, vi_path) == ([], [])


# --- callee_path: set from the caller's own record at projection time ---


def _call_node(callee_key: str | None) -> VINode:
    return VINode(id="10", vi_path="x", callee_key=callee_key)


def test_projection_sets_callee_path_only_for_a_callee_in_the_project(tmp_path: Path):
    root = tmp_path.resolve()
    caller = str(root / "Caller.vi")
    inside = str(root / "sub" / "Callee.vi")
    outside = str(tmp_path.parent / "elsewhere" / "Other.vi")
    in_project = build._in_project_callee_path
    assert in_project(_call_node(inside), caller, root) == inside
    assert in_project(_call_node(outside), caller, root) is None
    assert in_project(_call_node(caller), caller, root) is None
    assert in_project(_call_node(None), caller, root) is None


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
                callee_key=str(callee),
                callee_path=callee_path,
            )
        ],
    )


def _loaded_callee_paths(root: Path, path: Path) -> list[str | None]:
    [loaded] = [f for f in load_index(root) if f.path == str(path)]
    return [n.callee_path for n in loaded.nodes]


class _OneVIGraph:
    """Just enough of a loaded graph for the warm functions: one VI on disk and
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


def _project_like_the_real_one(
    monkeypatch: pytest.MonkeyPatch, caller: Path, callee: Path, root: Path
) -> list[str]:
    """Replace the graph read with a projection whose call node goes through the
    real callee-path rule; returns the VIs it projected."""
    projected: list[str] = []

    def _project(graph, vi_name, vi_path, project_root) -> VIFacts:
        projected.append(vi_name)
        path = build._in_project_callee_path(
            _call_node(str(callee)), str(caller), root.resolve()
        )
        return _caller(caller, callee, path)

    monkeypatch.setattr(build, "project_vi_facts", _project)
    return projected


@pytest.mark.parametrize("warm", [_warm_all_loaded, _warm_index_for_vi])
@pytest.mark.parametrize("synced_first", [True, False])
def test_progressive_write_has_the_callee_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warm: Callable[[_OneVIGraph], None],
    synced_first: bool,
):
    """Loading a caller (describe, render, read_vi) writes its SubVI call with
    the callee's path, whether or not a full sync ran before."""
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    caller = (root / "Caller.vi").resolve()
    callee = (root / "Callee.vi").resolve()
    caller.write_bytes(b"caller")
    callee.write_bytes(b"callee")
    if synced_first:
        save_index(root, [_caller(caller, callee, str(callee))])
    projected = _project_like_the_real_one(monkeypatch, caller, callee, root)
    graph = _OneVIGraph(caller)
    warm(graph)

    assert projected == ["Caller.vi"] and graph.typedefs_listed
    assert _loaded_callee_paths(root, caller) == [str(callee)]


def test_full_sync_clears_a_callee_that_left_the_project(tmp_path: Path):
    """A full sync keeps a callee path only for an indexed VI: when the callee
    is gone, the path stored for unchanged caller bytes is cleared."""
    caller = tmp_path / "Caller.vi"
    callee = tmp_path / "Callee.vi"
    stored = _caller(caller, callee, str(callee))
    save_index(tmp_path, [stored])

    synced = _caller(caller, callee, str(callee))
    resolve_node_callee_paths([synced])
    save_index(tmp_path, [synced])

    assert _loaded_callee_paths(tmp_path, caller) == [None]


# --- a full refresh writes the recalculated scores for unchanged bytes ---


def test_sync_writes_recalculated_scores_for_unchanged_bytes(tmp_path: Path):
    vi_path = tmp_path / "Sub.vi"
    save_index(tmp_path, [VIFacts(path=str(vi_path), name="Sub.vi", content_sha="abc")])

    recomputed = VIFacts(
        path=str(vi_path),
        name="Sub.vi",
        content_sha="abc",
        impact_score=5,
        callers_count=2,
        scores_computed=True,
    )
    save_index(tmp_path, [recomputed])

    [loaded] = load_index(tmp_path)
    assert (loaded.impact_score, loaded.callers_count) == (5, 2)


def test_progressive_write_keeps_the_stored_scores(tmp_path: Path):
    vi_path = tmp_path / "Sub.vi"
    save_index(
        tmp_path,
        [
            VIFacts(
                path=str(vi_path),
                name="Sub.vi",
                content_sha="abc",
                impact_score=5,
                callers_count=2,
                scores_computed=True,
            )
        ],
    )
    save_index(tmp_path, [VIFacts(path=str(vi_path), name="Sub.vi", content_sha="abc")])

    [loaded] = load_index(tmp_path)
    assert (loaded.impact_score, loaded.callers_count) == (5, 2)
