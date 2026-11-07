"""An edit to a project's ``.lvkit/`` catalog refreshes the cached render and the
index rows of the VIs that use the edited entry, and only those."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from lvkit.build_options import BuildOptions
from lvkit.catalog_keys import catalog_keys
from lvkit.catalog_stamp import (
    CatalogKey,
    catalog_stamp,
    catalog_stamp_fresh,
    project_entry_hashes,
)
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, VINode
from lvkit.index.build import sync_index
from lvkit.index.project import resolve_project_files
from lvkit.index.store import facts_digest
from lvkit.models import LVType, LVTypeKind, Terminal, TerminalDirection
from lvkit.output_cache import (
    cached_render,
    lookup_render,
    render_options_tag,
)
from lvkit.primitive_resolver import PrimitiveResolver
from lvkit.project_store import init_project_store
from lvkit.run_report import RunProblems, RunSettings

_SAMPLES = Path(".lvkit/cache/samples/JKI-VI-Tester/source/Build Support")
_DIR = _SAMPLES / "Package Builder Utilities"
# Uses primitive 1116 ("Not Equal To 0?") and not 1535.
_A = _DIR / "Error Cluster From Error Code__JKI_RIGHT_CLICK_BUILD_SUPPORT.vi"
# Uses primitive 1535 and not 1116.
_B = _DIR / "Trim Whitespace__JKI_RIGHT_CLICK_BUILD_SUPPORT.vi"
_P = "1116"
_Q = "1535"


def _write_entry(store: Path, prim_id: str, name: str) -> None:
    """Make the store's ``primitives.json`` define ``prim_id`` as ``name`` (the
    other entries it defines stay)."""
    path = store / "primitives.json"
    data = json.loads(path.read_text()) if path.exists() else {"primitives": {}}
    entry = dict(PrimitiveResolver().get_by_id(int(prim_id)) or {})
    entry["name"] = name
    data["primitives"][prim_id] = entry
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_keys_of_a_vi_are_its_primitive_its_vilib_call_and_its_typedef() -> None:
    graph = InMemoryVIGraph()
    typed = Terminal(
        id="x.vi::2:t",
        index=0,
        direction=TerminalDirection.OUTPUT,
        lv_type=LVType(
            kind=LVTypeKind.ENUM, underlying_type="Enum", typedef_name="E.ctl"
        ),
    )
    prim = PrimitiveNode(id="x.vi::1", vi_path="x.vi", prim_id=1116, terminals=[typed])
    call = VINode(
        id="x.vi::3",
        vi_path="x.vi",
        name="Foo.vi",
        qualified_path="<vilib>/Utility/error.llb/Foo.vi",
    )
    for node in (prim, call):
        graph._graph.add_node(node.id, node=node)
    graph._vi_nodes["x.vi"] = {prim.id, call.id}

    assert catalog_keys(graph, "x.vi") == {
        CatalogKey("primitive", "1116"),
        CatalogKey("vilib", "Foo.vi"),
        CatalogKey("type", "E.ctl"),
    }


def test_an_edit_changes_the_hash_of_that_entry_only(tmp_path: Path) -> None:
    store = init_project_store(tmp_path)
    _write_entry(store, _P, "first")
    before = project_entry_hashes(store)
    _write_entry(store, _Q, "other")
    added = project_entry_hashes(store)
    assert added[CatalogKey("primitive", _P)] == before[CatalogKey("primitive", _P)]
    assert CatalogKey("primitive", _Q) in added
    _write_entry(store, _P, "second")
    edited = project_entry_hashes(store)
    assert edited[CatalogKey("primitive", _P)] != added[CatalogKey("primitive", _P)]
    assert edited[CatalogKey("primitive", _Q)] == added[CatalogKey("primitive", _Q)]


def test_a_stamp_is_stale_for_an_edited_key_or_a_key_that_gains_an_entry(
    tmp_path: Path,
) -> None:
    store = init_project_store(tmp_path)
    _write_entry(store, _P, "first")
    uses_p = catalog_stamp(store, [CatalogKey("primitive", _P)])
    uses_q = catalog_stamp(store, [CatalogKey("primitive", _Q)])
    assert catalog_stamp_fresh(store, uses_p) and catalog_stamp_fresh(store, uses_q)
    _write_entry(store, _Q, "added")
    assert catalog_stamp_fresh(store, uses_p)
    assert not catalog_stamp_fresh(store, uses_q)
    _write_entry(store, _P, "second")
    assert not catalog_stamp_fresh(store, uses_p)


def _project(root: Path) -> tuple[Path, Path, Path]:
    """A project with the two sample VIs and a store defining primitive 1116."""
    (root / ".git").mkdir(parents=True)
    store = init_project_store(root)
    _write_entry(store, _P, "first")
    for source in (_A, _B):
        shutil.copy(source, root / source.name)
    return store, root / _A.name, root / _B.name


@pytest.mark.needs_samples
@pytest.mark.cold_cache
def test_an_edit_rebuilds_the_index_rows_of_the_vis_that_use_the_entry(
    tmp_path: Path,
) -> None:
    if not _A.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    store, a, b = _project(tmp_path / "proj")
    root = tmp_path / "proj"

    def sync() -> tuple[set[str], dict[str, str]]:
        run = RunProblems(RunSettings(project_store=store))
        _, vis, ctls = resolve_project_files(root)
        done = sync_index(root, vis, ctls, run=run)
        # a cold store is one full build: every VI is indexed, none "rebuilt"
        rebuilt = set(done.refresh.rebuilt) if done.refresh is not None else set()
        return rebuilt, {f.path: facts_digest(f) for f in done.facts}

    _, first = sync()
    assert set(first) == {str(a.resolve()), str(b.resolve())}
    again, digests = sync()
    assert again == set()

    _write_entry(store, _P, "second")
    edited, after_edit = sync()
    assert edited == {str(a.resolve())}
    assert after_edit[str(b.resolve())] == digests[str(b.resolve())]

    _write_entry(store, _Q, "added")
    added, _ = sync()
    assert added == {str(b.resolve())}
    nothing, _ = sync()
    assert nothing == set()


@pytest.mark.needs_samples
@pytest.mark.cold_cache
def test_an_edit_makes_the_cached_render_stale_only_for_a_vi_that_uses_it(
    tmp_path: Path,
) -> None:
    if not _A.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    store, a, _ = _project(tmp_path / "proj")
    build = BuildOptions(project_store=store)
    options = render_options_tag("svg", "light", None, build)
    settings = RunSettings(project_store=store)

    def render() -> None:
        cached_render(
            a,
            fmt="svg",
            options=options,
            version="test",
            return_cached=False,
            run_settings=settings,
            theme_mode="light",
            **build.build_kwargs(),
        )

    def hit() -> bool:
        return lookup_render(a, "svg", options, "test", store) is not None

    render()
    assert hit()
    unrelated = store / "primitives.json"
    data = json.loads(unrelated.read_text())
    data["primitives"]["99999"] = {"name": "Nobody uses this"}
    unrelated.write_text(json.dumps(data), encoding="utf-8")
    assert hit()
    _write_entry(store, _P, "second")
    assert not hit()
