"""``.ctl`` controls in the index: facts, incremental sync, and the ``typedef*``
views (backed by the sample corpus)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lvkit.graph import load_vi_by_path
from lvkit.graph.loading import LoadMode
from lvkit.index import sql as isql
from lvkit.index import typedefs as typedefs_mod
from lvkit.index.build import project_vi_facts, sync_index, warm_all_loaded
from lvkit.index.project import resolve_project_files
from lvkit.index.store import load_typedef_shas
from lvkit.index.typedefs import build_typedef_facts, sync_typedefs, warm_typedefs
from lvkit.models import LVTypeKind

from .conftest import SAMPLES_ROOT

pytestmark = pytest.mark.needs_samples

_ICON_API = SAMPLES_ROOT / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API"
_CLUSTER_CTL = _ICON_API / "API_Test Settings.ctl"  # uses Alignment.ctl (nested)
_ALIGNMENT_CTL = _ICON_API / "lv_icon/Controls/Alignment.ctl"
_ENUM_CTL = SAMPLES_ROOT / "DCAF-DAQModule/source/editor node/Permissions Enum.ctl"
_USER_VI = _ICON_API / "Generate LV Icon Template Layer.vi"


def _ctls(root: Path) -> list[Path]:
    return resolve_project_files(root)[2]


def _q(root: Path, sql: str) -> list[list[object]]:
    return isql.run_query(root, sql).rows


@pytest.fixture
def project(tmp_path: Path) -> Path:
    if not (_CLUSTER_CTL.exists() and _ENUM_CTL.exists()):
        pytest.skip("samples absent")
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copy(_CLUSTER_CTL, root / "Settings.ctl")
    shutil.copy(_ENUM_CTL, root / "Mode.ctl")
    return root


def test_a_cluster_control_is_indexed_with_its_fields_and_type(project: Path) -> None:
    fact = build_typedef_facts(project, project / "Settings.ctl")
    assert not fact.is_stub and fact.kind is LVTypeKind.CLUSTER and fact.type_id
    names = {f.name for f in fact.fields}
    assert {"Font", "Size", "Text color"} <= names
    nested = [f for f in fact.fields if f.depth == 1]
    assert nested and all(f.parent_seq is not None for f in nested)


def test_sync_stores_the_controls_and_the_views_answer(project: Path) -> None:
    sync_typedefs(project, _ctls(project))
    assert sorted(str(r[0]) for r in _q(project, "SELECT name FROM typedef")) == [
        "Mode.ctl",
        "Settings.ctl",
    ]
    # a control's fields, with nesting and recorded defaults
    fields = _q(
        project,
        "SELECT f.name, f.depth FROM typedef_field f JOIN typedef t "
        "ON t.path = f.typedef_path WHERE t.name='Settings.ctl' ORDER BY f.seq",
    )
    assert ["Font", 0] in fields and any(r[1] == 1 for r in fields)
    # an enum control's type carries its items
    items = _q(
        project,
        "SELECT i.name FROM typedef t JOIN type_item i USING (type_id) "
        "WHERE t.name='Mode.ctl' ORDER BY i.seq",
    )
    assert items == [["Read from HW"], ["Write to HW"]]
    # controls that contain a type (own or nested): a plain filter, no recursion
    assert _q(
        project,
        "SELECT DISTINCT t.name FROM typedef t JOIN typedef_type tt "
        "ON tt.typedef_path = t.path JOIN type ty USING (type_id) "
        "WHERE ty.kind='enum' ORDER BY 1",
    ) == [["Mode.ctl"]]


def test_sync_is_incremental_and_drops_gone_controls(project: Path) -> None:
    first = sync_typedefs(project, _ctls(project))
    assert (first.controls, len(first.rebuilt), len(first.deleted)) == (2, 2, 0)
    shas = load_typedef_shas(project)
    (project / "Mode.ctl").unlink()
    second = sync_typedefs(project, _ctls(project))
    settings = str((project / "Settings.ctl").resolve())
    assert (second.controls, len(second.rebuilt), len(second.deleted)) == (1, 0, 1)
    assert load_typedef_shas(project) == {settings: shas[settings]}  # kept as is
    assert _q(project, "SELECT count(*) FROM typedef") == [[1]]
    assert _q(
        project, "SELECT count(*) FROM type WHERE kind='enum' AND name IS NOT NULL"
    ) == [[0]]  # the enum control's types were collected with it

    # a changed file is rebuilt
    shutil.copy(_ENUM_CTL, project / "Settings.ctl")
    assert len(sync_typedefs(project, _ctls(project)).rebuilt) == 1
    assert _q(project, "SELECT kind FROM typedef") == [["enum"]]


def test_a_forced_rebuild_still_drops_gone_controls(project: Path) -> None:
    sync_typedefs(project, _ctls(project))
    (project / "Mode.ctl").unlink()
    result = sync_typedefs(project, _ctls(project), rebuild=True)
    assert (len(result.rebuilt), len(result.deleted)) == (1, 1)
    assert _q(project, "SELECT name FROM typedef") == [["Settings.ctl"]]
    assert _q(
        project,
        "SELECT count(*) FROM typedef_field f WHERE NOT EXISTS "
        "(SELECT 1 FROM typedef t WHERE t.path = f.typedef_path)",
    ) == [[0]]


def test_an_unreadable_control_is_a_stub_with_its_reason(project: Path) -> None:
    (project / "Broken.ctl").write_bytes(b"not a labview file")
    sync_typedefs(project, _ctls(project))
    rows = _q(
        project,
        "SELECT is_stub, stub_reason IS NOT NULL FROM typedef WHERE name='Broken.ctl'",
    )
    assert rows == [[1, 1]]
    assert _q(
        project,
        "SELECT count(*) FROM typedef_field f JOIN typedef t "
        "ON t.path = f.typedef_path WHERE t.name='Broken.ctl'",
    ) == [[0]]


@pytest.mark.skipif(not _USER_VI.exists(), reason="icon-editor sample absent")
def test_a_vi_records_the_controls_it_depends_on() -> None:
    graph, vi = load_vi_by_path(_USER_VI, LoadMode.MINIMAL, search_paths=[_ICON_API])
    facts = project_vi_facts(graph, vi, _USER_VI, _ICON_API)
    assert facts.typedef_paths
    assert all(Path(p).suffix == ".ctl" for p in facts.typedef_paths)
    assert facts.typedef_paths == sorted(set(facts.typedef_paths))
    versions = {v.typedef_path: v.type_id for v in facts.typedef_versions}
    assert set(facts.typedef_paths) <= set(versions)
    assert all(type_id for type_id in versions.values())  # every one resolved
    # Recording versions must not pollute the VI's OWN type closure: a
    # dependency's root type counts as used only if a terminal/constant of
    # this VI actually reaches it, not merely because the VI depends on the
    # control at all.
    unreachable = set(versions.values()) - set(facts.type_ids)
    assert unreachable, "fixture should have a dependency not on the connector pane"


@pytest.mark.skipif(not _USER_VI.exists(), reason="icon-editor sample absent")
def test_loading_a_vi_warms_the_controls_it_uses(tmp_path: Path) -> None:
    """A progressively-warmed index holds a ``typedef`` row for every control a
    VI's ``typedef_use`` names."""
    root = tmp_path / "proj"
    shutil.copytree(_ICON_API, root)
    (root / ".git").mkdir()  # marks the project root, so VIs and controls share one
    vi = root / _USER_VI.name
    graph, _ = load_vi_by_path(vi, LoadMode.MINIMAL, search_paths=[root])
    warm_all_loaded(graph)
    assert _q(root, "SELECT count(*) FROM typedef") != [[0]]
    assert _q(
        root,
        "SELECT count(*) FROM typedef_use u WHERE NOT EXISTS "
        "(SELECT 1 FROM typedef t WHERE t.path = u.typedef_path)",
    ) == [[0]]


def test_any_failure_reading_a_control_is_a_stub(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*_a: object, **_k: object) -> None:
        raise KeyError("boom")

    monkeypatch.setattr(typedefs_mod, "load_ctl_by_path", explode)
    fact = build_typedef_facts(project, project / "Settings.ctl")
    assert fact.is_stub and fact.stub_reason and "KeyError" in fact.stub_reason


def test_warming_skips_controls_already_indexed(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warm_typedefs(project, _ctls(project))
    built: list[Path] = []
    real = typedefs_mod.build_typedef_facts
    monkeypatch.setattr(
        typedefs_mod,
        "build_typedef_facts",
        lambda root, p, sha=None: built.append(p) or real(root, p, sha),
    )
    warm_typedefs(project, _ctls(project))
    assert built == []
    shutil.copy(_ENUM_CTL, project / "Settings.ctl")
    warm_typedefs(project, _ctls(project))
    assert [p.name for p in built] == ["Settings.ctl"]


def _icon_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    shutil.copytree(_ICON_API, root)
    (root / ".git").mkdir()
    return root


@pytest.mark.skipif(not _USER_VI.exists(), reason="icon-editor sample absent")
def test_a_changed_control_rebuilds_the_vis_that_use_it(tmp_path: Path) -> None:
    root = _icon_project(tmp_path)
    vi = (root / _USER_VI.name).resolve()

    def sync(rebuild: bool = False):  # noqa: ANN202
        _, vis, ctls = resolve_project_files(root)
        return sync_index(root, vis, ctls, rebuild=rebuild)

    first = sync()
    used = next(f for f in first.facts if f.path == str(vi)).typedef_paths
    assert used
    # the VI file is untouched; only a control it uses changes
    shutil.copy(_ENUM_CTL, used[0])
    second = sync()
    assert second.refresh is not None and str(vi) in second.refresh.rebuilt
    # and an untouched project rebuilds nothing
    third = sync()
    assert third.refresh is not None and third.refresh.rebuilt == []


@pytest.mark.skipif(not _USER_VI.exists(), reason="icon-editor sample absent")
def test_a_forced_rebuild_drops_vis_whose_file_is_gone(tmp_path: Path) -> None:
    root = _icon_project(tmp_path)
    _, vis, ctls = resolve_project_files(root)
    sync_index(root, vis, ctls)
    gone = next(v for v in vis if v.name != _USER_VI.name)
    gone.unlink()
    _, vis, ctls = resolve_project_files(root)
    sync_index(root, vis, ctls, rebuild=True)
    assert _q(root, f"SELECT count(*) FROM vi WHERE path = '{gone.resolve()}'") == [[0]]


@pytest.fixture
def nested_project(tmp_path: Path) -> Path:
    """A control that uses another (real corpus nesting): ``API_Test
    Settings.ctl`` uses ``Alignment.ctl``."""
    if not (_CLUSTER_CTL.exists() and _ALIGNMENT_CTL.exists()):
        pytest.skip("samples absent")
    root = tmp_path / "nested"
    root.mkdir()
    shutil.copy(_CLUSTER_CTL, root / _CLUSTER_CTL.name)
    shutil.copy(_ALIGNMENT_CTL, root / _ALIGNMENT_CTL.name)
    return root


def test_a_change_to_a_nested_control_rebuilds_its_user(nested_project: Path) -> None:
    """``API_Test Settings.ctl`` records a ``uses`` edge to ``Alignment.ctl``
    (a real corpus nesting). Only the nested control's bytes change here; the
    outer file is untouched -- it must still be rebuilt, because its own
    fields/type_id are resolved (in general) through what it uses, not only
    from its own bytes. (For this specific real pair the outer happens to embed
    its own field snapshot, so the id itself does not move here -- see
    test_a_typedef_reference_is_resolved_through_the_graph_callback in
    test_index_types.py for the case where it does. The rebuild is what this
    test guards: without it, a used control's change is never even looked at.)
    """
    outer = (nested_project / _CLUSTER_CTL.name).resolve()
    inner = nested_project / _ALIGNMENT_CTL.name
    sync_typedefs(nested_project, _ctls(nested_project))
    shutil.copy(_ENUM_CTL, inner)
    result = sync_typedefs(nested_project, _ctls(nested_project))
    assert str(outer) in result.rebuilt


@pytest.mark.skipif(not _USER_VI.exists(), reason="icon-editor sample absent")
def test_a_control_changed_only_by_a_warm_still_invalidates_its_users(
    tmp_path: Path,
) -> None:
    """A progressive warm (``render``/``describe``/an MCP deep tool) can update a
    control's row without going through ``sync_index``'s own bookkeeping of what
    it just touched -- the next sync must still catch the mismatch."""
    root = _icon_project(tmp_path)
    _, vis, ctls = resolve_project_files(root)
    first = sync_index(root, vis, ctls)
    vi = (root / _USER_VI.name).resolve()
    used = next(f for f in first.facts if f.path == str(vi)).typedef_paths
    assert used
    target = Path(used[0])
    shutil.copy(_ENUM_CTL, target)
    warm_typedefs(root, [target])  # bypasses sync_index entirely
    _, vis, ctls = resolve_project_files(root)
    second = sync_index(root, vis, ctls)
    assert second.refresh is not None and str(vi) in second.refresh.rebuilt


@pytest.mark.skipif(not _USER_VI.exists(), reason="icon-editor sample absent")
def test_a_control_that_becomes_readable_invalidates_its_users(tmp_path: Path) -> None:
    root = _icon_project(tmp_path)
    _, vis, ctls = resolve_project_files(root)
    first = sync_index(root, vis, ctls)
    vi = (root / _USER_VI.name).resolve()
    used = next(f for f in first.facts if f.path == str(vi)).typedef_paths
    assert used
    target = Path(used[0])
    original = target.read_bytes()
    target.write_bytes(b"not a labview file")
    _, vis, ctls = resolve_project_files(root)
    sync_index(root, vis, ctls)  # the control is now a stub; the VI itself is untouched
    target.write_bytes(original)  # readable again
    _, vis, ctls = resolve_project_files(root)
    third = sync_index(root, vis, ctls)
    assert third.refresh is not None and str(vi) in third.refresh.rebuilt


@pytest.mark.skipif(not _USER_VI.exists(), reason="icon-editor sample absent")
def test_warming_never_writes_a_control_outside_the_project(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".git").mkdir()
    shutil.copy(_USER_VI, root / _USER_VI.name)
    graph, _ = load_vi_by_path(
        root / _USER_VI.name, LoadMode.MINIMAL, search_paths=[_ICON_API]
    )
    warm_all_loaded(graph)
    assert _q(root, "SELECT count(*) FROM typedef") == [[0]]
