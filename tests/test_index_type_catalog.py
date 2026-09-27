"""The type catalog in the index: types stored once and shared, per-VI closures,
garbage collection, and the ``type`` / ``type_field`` / ``type_item`` / ``vi_used_type``
views."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.index import sql as isql
from lvkit.index.build import build_index
from lvkit.index.model import ConstantFact, TerminalFact, VIFacts
from lvkit.index.project import resolve_project
from lvkit.index.store import delete as delete_index
from lvkit.index.store import load as load_index
from lvkit.index.store import save as save_index
from lvkit.index.types import TypeCatalog
from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind

from .conftest import SAMPLES_ROOT

_DBL = LVType(LVTypeKind.PRIMITIVE, underlying_type="DBL")
_STR = LVType(LVTypeKind.PRIMITIVE, underlying_type="String")
_MODE = LVType(
    LVTypeKind.ENUM,
    typedef_name="Mode.ctl",
    values={"Read": EnumValue(0), "Write": EnumValue(1)},
)


def _cluster(*fields: tuple[str, LVType], name: str | None = None) -> LVType:
    return LVType(
        LVTypeKind.CLUSTER,
        typedef_name=name,
        fields=[ClusterField(n, t) for n, t in fields],
    )


def _vi(path: str, *terminal_types: LVType) -> VIFacts:
    catalog = TypeCatalog()
    terminals = [
        TerminalFact(
            name=f"t{i}",
            direction="input",
            is_indicator=False,
            is_public=True,
            control_type=None,
            type_descriptor=t.type_descriptor(),
            type_kind=t.kind,
            type_id=catalog.add(t),
        )
        for i, t in enumerate(terminal_types)
    ]
    constants = [ConstantFact(value="1", label=None, type_id=catalog.add(_DBL))]
    return VIFacts(
        path=path,
        name=Path(path).name,
        terminals=terminals,
        constants=constants,
        type_ids=sorted(catalog.types),
        types=list(catalog.types.values()),
    )


def _q(root: Path, sql: str) -> list[list[object]]:
    return isql.run_query(root, sql).rows


def _count(root: Path, table: str, where: str = "1=1") -> int:
    (row,) = _q(root, f"SELECT count(*) FROM {table} WHERE {where}")
    return int(row[0])  # type: ignore[call-overload]


def _users(root: Path, type_id: object) -> list[str]:
    rows = _q(root, f"SELECT vi_path FROM vi_used_type WHERE type_id='{type_id}'")
    return sorted(str(r[0]) for r in rows)


def test_ids_and_closures_round_trip_through_the_store(tmp_path: Path) -> None:
    outer = _cluster(("mode", _MODE), ("gain", _DBL), name="Config.ctl")
    vi = _vi("/p/a.vi", outer)
    save_index(tmp_path, [vi])
    loaded = load_index(tmp_path)[0]
    assert [t.type_id for t in loaded.terminals] == [vi.terminals[0].type_id]
    assert loaded.constants[0].type_id == vi.constants[0].type_id
    assert loaded.type_ids == vi.type_ids
    assert loaded.types == []  # bodies live in the shared `types` table, not on the VI


def test_a_shared_type_is_stored_once_and_collected_when_unused(tmp_path: Path) -> None:
    a = _vi("/p/a.vi", _MODE)
    b = _vi("/p/b.vi", _MODE, _STR)
    save_index(tmp_path, [a, b])
    mode_id = a.terminals[0].type_id
    assert _count(tmp_path, "type", f"type_id='{mode_id}'") == 1
    assert _users(tmp_path, mode_id) == ["/p/a.vi", "/p/b.vi"]

    delete_index(tmp_path, ["/p/b.vi"])
    assert _count(tmp_path, "type", f"type_id='{mode_id}'") == 1
    string_id = b.terminals[1].type_id
    assert _count(tmp_path, "type", f"type_id='{string_id}'") == 0

    delete_index(tmp_path, ["/p/a.vi"])
    assert _count(tmp_path, "type") == 0
    assert _count(tmp_path, "type_item") == 0


def test_resaving_a_vi_drops_the_types_it_no_longer_uses(tmp_path: Path) -> None:
    save_index(tmp_path, [_vi("/p/a.vi", _MODE)])
    save_index(tmp_path, [_vi("/p/a.vi", _STR)])
    assert _count(tmp_path, "type", "name IS NOT NULL") == 0
    assert _count(tmp_path, "type_item") == 0


def test_the_views_answer_structure_questions(tmp_path: Path) -> None:
    config = _cluster(("mode", _MODE), ("gain", _DBL), name="Config.ctl")
    other = _cluster(("mode", _MODE), ("label", _STR))  # anonymous, also has `mode`
    save_index(tmp_path, [_vi("/p/a.vi", config), _vi("/p/b.vi", other)])

    # every enum containing an item
    assert _q(
        tmp_path,
        "SELECT t.name FROM type t JOIN type_item i USING (type_id) "
        "WHERE t.kind='enum' AND i.name='Write'",
    ) == [["Mode.ctl"]]

    # a VI that uses a type NESTED inside its terminal's type: no recursion needed
    mode_id = _q(tmp_path, "SELECT type_id FROM type WHERE name='Mode.ctl'")[0][0]
    assert _users(tmp_path, mode_id) == ["/p/a.vi", "/p/b.vi"]

    # clusters that have BOTH fields `mode` and `gain`
    has = (
        "EXISTS (SELECT 1 FROM type_field f WHERE f.type_id=t.type_id AND f.name='{}')"
    )
    assert _q(
        tmp_path,
        "SELECT t.name FROM type t WHERE t.kind='cluster' "
        f"AND {has.format('mode')} AND {has.format('gain')}",
    ) == [["Config.ctl"]]

    # terminals expose the id, so a terminal joins straight to its type
    assert _q(
        tmp_path,
        "SELECT DISTINCT t.name FROM terminal x JOIN type t USING (type_id) "
        "WHERE x.vi_path='/p/a.vi' AND t.kind='cluster'",
    ) == [["Config.ctl"]]


_TESTCASE_DIR = SAMPLES_ROOT / "JKI-VI-Tester" / "source" / "Classes" / "TestCase"


@pytest.mark.needs_samples
@pytest.mark.skipif(not _TESTCASE_DIR.exists(), reason="JKI sample absent")
def test_a_real_directory_is_indexed_with_types(tmp_path):
    root, vi_paths = resolve_project(_TESTCASE_DIR)
    result = build_index(root, vi_paths)
    save_index(root, result.facts)
    resolved = [t for f in result.facts for t in f.terminals if t.type_descriptor]
    assert resolved and all(t.type_id for t in resolved)  # each resolved type has an id
    assert _count(root, "type") > 0
    assert _count(root, "terminal", "type_id IS NULL AND type_descriptor != ''") == 0
    # each stored type's parts point at types that exist (or are unresolved)
    dangling = _q(
        root,
        "SELECT count(*) FROM type_field f WHERE f.field_type_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM type t WHERE t.type_id=f.field_type_id)",
    )
    assert dangling == [[0]]


def test_a_vi_naming_a_type_the_catalog_lacks_is_refused(tmp_path: Path) -> None:
    facts = _vi(str(tmp_path / "a.vi"), _MODE)
    facts.types = []  # the bodies are gone but the ids are still named
    with pytest.raises(ValueError, match="not in the catalog"):
        save_index(tmp_path, [facts])
