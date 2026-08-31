"""Tests for lvkit.index.sql — the read-only SQL query surface.

Mostly synthetic (hand-built ``VIFacts`` saved to the hermetic per-test index
DB), so they're fast and need no sample corpus. The sandbox tests are the
load-bearing ones: they prove writes/PRAGMA/ATTACH/stacked-statements are
refused STRUCTURALLY, not by grepping the SQL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.index import sql
from lvkit.index import store as store_mod
from lvkit.index.model import (
    OUTPUT,
    ConstantFact,
    NodeFact,
    NodeKind,
    TerminalFact,
    VIFacts,
    WiredTo,
)
from lvkit.index.store import load as load_index
from lvkit.index.store import save as save_index
from lvkit.models import LVTypeKind


def _term(
    name: str,
    *,
    direction: str = OUTPUT,
    error: bool = False,
    type_descriptor: str = "?",
    type_kind: LVTypeKind | None = None,
    enum_values: list[str] | None = None,
) -> TerminalFact:
    # An error cluster is duck-typed — it surfaces as the built-in-style
    # descriptor 'Error' (kind CLUSTER), never a distinct nominal type.
    if error:
        type_descriptor = "Error"
        type_kind = LVTypeKind.CLUSTER
    return TerminalFact(
        name=name,
        direction=direction,
        is_indicator=(direction == OUTPUT),
        is_public=True,
        control_type=None,
        type_descriptor=type_descriptor,
        type_kind=type_kind,
        enum_values=enum_values or [],
    )


def _project(tmp_path: Path) -> Path:
    """Build a tiny synthetic index under a hermetic root and return the root."""
    facts = [
        VIFacts(
            path=str(tmp_path / "a.vi"),
            name="a.vi",
            qualified_name="Lib.lvlib:a.vi",
            library="Lib.lvlib",
            content_sha="sha-a",
            terminals=[
                _term("error out", error=True),
                _term("error out", error=True),
                _term("result"),  # not an error cluster
                _term("error in", direction="input", error=True),  # input side
            ],
            constants=[
                ConstantFact(
                    value="42",
                    label="answer",
                    type_descriptor="I32",
                    type_kind=LVTypeKind.PRIMITIVE,
                    wired_to=WiredTo.INDICATOR,
                ),
            ],
            nodes=[
                NodeFact(
                    uid="call_b",
                    kind=NodeKind.VI,
                    name="b.vi",
                    qualified_name="Lib.lvlib:b.vi",
                    callee_path=str(tmp_path / "b.vi"),
                )
            ],
        ),
        VIFacts(
            path=str(tmp_path / "b.vi"),
            name="b.vi",
            qualified_name="Lib.lvlib:b.vi",
            library="Lib.lvlib",
            content_sha="sha-b",
            terminals=[
                _term("err", error=True),  # a third, differently-named error out
            ],
        ),
    ]
    save_index(tmp_path, facts)
    return tmp_path


# === Facts-version cache invalidation ======================================


def test_facts_version_steady_state_keeps_cache(tmp_path: Path):
    """With lvkit's code unchanged, the fingerprint matches on every connect, so
    the derived-facts cache survives — no spurious re-wipe/rebuild per query."""
    root = _project(tmp_path)
    n1 = len(load_index(root))
    assert n1 > 0
    # A second connect (unchanged fingerprint) must return the SAME rows, proving
    # it didn't drop the tables and hand back an empty cache.
    assert len(load_index(root)) == n1


def test_facts_version_invalidates_when_extraction_code_changes(
    tmp_path: Path, monkeypatch
):
    """When lvkit's facts-producing source changes (fingerprint differs) the
    whole cache is dropped on the next connect — even though the VI bytes are
    identical. This is the guard that stopped a parser improvement from silently
    serving stale types to queries, the MCP server, and evals.
    """
    root = _project(tmp_path)
    assert load_index(root)  # populated under the real (matching) fingerprint

    # Simulate an edit to lvkit's extraction code: the fingerprint no longer
    # matches what stamped this DB.
    monkeypatch.setattr(store_mod, "_facts_fingerprint", lambda: "changed-code")

    # Next connect sees the mismatch and wipes the stale cache to force a cold
    # rebuild; the content_sha of each VI is unchanged, so ONLY the fingerprint
    # could have triggered this.
    assert load_index(root) == []


def test_facts_fingerprint_skips_only_non_facts_dirs():
    """The dirs the fingerprint SKIPS must be genuinely irrelevant to facts.

    Guards the ``_FINGERPRINT_SKIP_DIRS`` exclude list: if a module under a
    skipped dir ever enters the import closure of ``index.build``/``store`` (the
    code that produces facts), editing it would change facts yet leave the
    fingerprint untouched — silent staleness. Computed in a CLEAN subprocess so
    modules pytest imported for OTHER tests (codegen, mcp, …) can't pollute the
    closure and mask a real escape.
    """
    import json
    import subprocess
    import sys

    probe = (
        "import importlib, sys, json\n"
        "from pathlib import Path\n"
        "import lvkit\n"
        "importlib.import_module('lvkit.index.build')\n"
        "importlib.import_module('lvkit.index.store')\n"
        "pkg = Path(lvkit.__file__).resolve().parent\n"
        "dirs = set()\n"
        "for name, m in list(sys.modules.items()):\n"
        "    f = getattr(m, '__file__', None)\n"
        "    if not name.startswith('lvkit.') or not f:\n"
        "        continue\n"
        "    rel = Path(f).resolve().relative_to(pkg).parts\n"
        "    if len(rel) > 1:\n"
        "        dirs.add(rel[0])\n"
        "print(json.dumps(sorted(dirs)))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    closure_dirs = set(json.loads(out.stdout.strip().splitlines()[-1]))
    escaped = closure_dirs & store_mod._FINGERPRINT_SKIP_DIRS
    assert not escaped, (
        f"facts import-closure now reaches skipped dir(s) {escaped}; either move "
        f"the dependency out or drop it from _FINGERPRINT_SKIP_DIRS"
    )


# === Schema introspection ==================================================


def test_describe_schema_lists_all_views():
    views = {v.name for v in sql.describe_schema()}
    assert views == {
        "vi",
        "terminal",
        "constant",
        "node",
        "type_use",
        "class_fact",
        "lvproj",
    }


def test_declared_columns_are_actually_selectable(tmp_path: Path):
    """Every column describe_schema advertises must exist in the live view —
    catches drift between the description and the SELECT body."""
    root = _project(tmp_path)
    for view in sql.describe_schema():
        cols = ", ".join(c.name for c in view.columns)
        res = sql.run_query(root, f"SELECT {cols} FROM {view.name} LIMIT 0")
        assert res.columns == [c.name for c in view.columns]


# === Basic querying + the driving question =================================


def test_basic_select(tmp_path: Path):
    root = _project(tmp_path)
    res = sql.run_query(root, "SELECT name FROM vi ORDER BY name")
    assert res.columns == ["name"]
    assert res.rows == [["a.vi"], ["b.vi"]]
    assert res.row_count == 2
    assert res.truncated is False


def test_error_indicator_histogram_is_the_group_by_answer(tmp_path: Path):
    """The driving question: count the names used for error indicators — a
    3-row histogram, NOT the raw terminal rows. Input-side error clusters and
    non-error terminals are excluded."""
    root = _project(tmp_path)
    res = sql.error_indicator_histogram(root)
    assert res.columns == ["name", "n"]
    # 'error out' twice (both output-side error clusters), 'err' once.
    assert res.rows == [["error out", 2], ["err", 1]]


def test_group_by_via_arbitrary_sql_matches_canned(tmp_path: Path):
    root = _project(tmp_path)
    res = sql.run_query(
        root,
        "SELECT name, COUNT(*) AS n FROM terminal "
        "WHERE type_descriptor = 'Error' AND direction = 'output' "
        "GROUP BY name ORDER BY n DESC, name",
    )
    assert res.rows == [["error out", 2], ["err", 1]]


def test_constants_wired_to_indicator(tmp_path: Path):
    root = _project(tmp_path)
    res = sql.run_query(
        root, "SELECT value, label FROM constant WHERE wired_to = 'indicator'"
    )
    assert res.rows == [["42", "answer"]]


def test_type_descriptor_and_enum_values_round_trip(tmp_path: Path):
    """Schema v3 (#22): the exact ``type_descriptor`` and ordinal
    ``enum_values`` round-trip through save/load and are queryable via SQL."""
    facts = [
        VIFacts(
            path=str(tmp_path / "m.vi"),
            name="m.vi",
            content_sha="sha-m",
            terminals=[
                _term(
                    "method",
                    direction="input",
                    type_descriptor="MethodEnum{setUp, testMethod, tearDown}",
                    type_kind=LVTypeKind.ENUM,
                    enum_values=["setUp", "testMethod", "tearDown"],
                ),
                _term("result"),  # non-enum terminal keeps the defaults
            ],
        ),
    ]
    save_index(tmp_path, facts)

    res = sql.run_query(
        tmp_path,
        "SELECT name, type_descriptor, type_kind, enum_values "
        "FROM terminal ORDER BY name",
    )
    assert res.columns == ["name", "type_descriptor", "type_kind", "enum_values"]
    rows = {r[0]: (r[1], r[2], r[3]) for r in res.rows}
    assert rows["method"] == (
        "MethodEnum{setUp, testMethod, tearDown}",
        "enum",
        '["setUp", "testMethod", "tearDown"]',
    )
    assert rows["result"] == ("?", None, "[]")

    # Queryable: find terminals whose enum carries a given member.
    hits = sql.run_query(
        tmp_path,
        "SELECT name FROM terminal WHERE enum_values LIKE '%\"testMethod\"%'",
    )
    assert hits.rows == [["method"]]


def test_nodes_round_trip(tmp_path: Path):
    """The block-diagram node spine round-trips through save/load and answers
    the grep-not-read slices: prim_id / qualified_name filters, the event-in-
    while containment self-join, frame membership, and a recursive containment
    walk — all in SQL, no VI read."""
    facts = [
        VIFacts(
            path=str(tmp_path / "producer.vi"),
            name="producer.vi",
            content_sha="sha-p",
            nodes=[
                # An Event Structure nested inside a While Loop (the classic
                # event-handler loop). Order below is the iter_nodes order the
                # ``ord`` column must preserve.
                NodeFact(uid="loop0", kind=NodeKind.WHILE, name="While Loop"),
                NodeFact(
                    uid="ev0",
                    kind=NodeKind.EVENT,
                    name="Event Structure",
                    parent_uid="loop0",
                ),
                NodeFact(
                    uid="enq",
                    kind=NodeKind.PRIMITIVE,
                    prim_id=1234,
                    name="Enqueue Element",
                    parent_uid="ev0",
                    frame="1",
                ),
                NodeFact(
                    uid="sub",
                    kind=NodeKind.VI,
                    name="Send.vi",
                    qualified_name="Msg.lvclass:Send.vi",
                    callee_path=str(tmp_path / "Send.vi"),
                ),
            ],
        ),
    ]
    save_index(tmp_path, facts)

    # (1) ord preserves iter_nodes order.
    ordered = sql.run_query(tmp_path, "SELECT uid FROM node ORDER BY ord")
    assert ordered.rows == [["loop0"], ["ev0"], ["enq"], ["sub"]]

    # (2) prim_id is the robust primitive filter.
    prim = sql.run_query(tmp_path, "SELECT kind, name FROM node WHERE prim_id = 1234")
    assert prim.rows == [["primitive", "Enqueue Element"]]

    # (3) a SubVI producer via kind='vi' + qualified_name/callee_path.
    sub = sql.run_query(
        tmp_path,
        "SELECT qualified_name, callee_path FROM node WHERE kind = 'vi'",
    )
    assert sub.rows == [["Msg.lvclass:Send.vi", str(tmp_path / "Send.vi")]]

    # (4) event-structure-inside-a-while-loop via the parent_uid self-join.
    handler = sql.run_query(
        tmp_path,
        "SELECT c.name FROM node c JOIN node p "
        "ON c.parent_uid = p.uid AND c.vi_path = p.vi_path "
        "WHERE c.kind = 'event' AND p.kind = 'while'",
    )
    assert handler.rows == [["Event Structure"]]

    # (5) frame membership: what sits in frame 1 of the event structure.
    in_frame = sql.run_query(
        tmp_path,
        "SELECT name FROM node WHERE parent_uid = 'ev0' AND frame = '1'",
    )
    assert in_frame.rows == [["Enqueue Element"]]

    # (6) recursive containment walk: every node transitively inside loop0.
    inside = sql.run_query(
        tmp_path,
        "WITH RECURSIVE contained(uid) AS ("
        "  SELECT uid FROM node WHERE parent_uid = 'loop0'"
        "  UNION"
        "  SELECT n.uid FROM node n JOIN contained c ON n.parent_uid = c.uid"
        ") SELECT uid FROM contained ORDER BY uid",
    )
    assert inside.rows == [["enq"], ["ev0"]]


def test_constant_type_descriptor_round_trips(tmp_path: Path):
    """A constant carries the exact ``type_descriptor`` + ``type_kind``,
    same discipline as terminals (#8)."""
    facts = [
        VIFacts(
            path=str(tmp_path / "k.vi"),
            name="k.vi",
            content_sha="sha-k",
            constants=[
                ConstantFact(
                    value="0",
                    label="err",
                    type_descriptor="Error",
                    type_kind=LVTypeKind.CLUSTER,
                ),
                ConstantFact(
                    value="3",
                    label=None,
                    type_descriptor="I32",
                    type_kind=LVTypeKind.PRIMITIVE,
                ),
            ],
        ),
    ]
    save_index(tmp_path, facts)
    res = sql.run_query(
        tmp_path,
        "SELECT value, type_descriptor, type_kind FROM constant ORDER BY value",
    )
    rows = {r[0]: (r[1], r[2]) for r in res.rows}
    assert rows["0"] == ("Error", "cluster")
    assert rows["3"] == ("I32", "primitive")


def test_class_private_data_round_trips(tmp_path: Path):
    """A class method VI records its owning class's private-data fields (#8),
    rendered 'name: <type_descriptor>', round-tripped and queryable."""
    from lvkit.index.model import ClassFact

    facts = [
        VIFacts(
            path=str(tmp_path / "setUp.vi"),
            name="setUp.vi",
            content_sha="sha-s",
            class_fact=ClassFact(
                owning_class="TestCase.lvclass",
                private_data=["testMethodName: String", "isSkipped: TF"],
            ),
        ),
    ]
    save_index(tmp_path, facts)
    res = sql.run_query(tmp_path, "SELECT owning_class, private_data FROM class_fact")
    assert res.rows == [
        [
            "TestCase.lvclass",
            '["testMethodName: String", "isSkipped: TF"]',
        ]
    ]


def test_uncalled_column_flags_dead_code(tmp_path: Path):
    """#20: dead-code detection is a ``callers_count = 0`` filter on the ``vi``
    view (a real, store-round-tripped column), NOT a fragile
    ``qualified_name``/``callee_key`` name anti-join. The column persists and
    is queryable; ``callers_count = 0`` selects exactly the uncalled VIs even
    when ``qualified_name`` is NULL."""
    facts = [
        VIFacts(
            path=str(tmp_path / "entry.vi"),
            name="entry.vi",
            qualified_name=None,
            content_sha="s1",
            callers_count=0,
        ),
        VIFacts(
            path=str(tmp_path / "sub.vi"),
            name="sub.vi",
            qualified_name=None,
            content_sha="s2",
            callers_count=3,
        ),
    ]
    save_index(tmp_path, facts)

    dead = sql.run_query(tmp_path, "SELECT name FROM vi WHERE callers_count = 0")
    assert dead.rows == [["entry.vi"]]

    # The column round-trips (value, not just the DEFAULT).
    both = sql.run_query(tmp_path, "SELECT name, callers_count FROM vi ORDER BY name")
    assert both.rows == [["entry.vi", 0], ["sub.vi", 3]]

    # The canned helper is the same filter.
    canned = sql.uncalled_vis(tmp_path)
    assert canned.columns == ["path", "name", "library"]
    assert [r[1] for r in canned.rows] == ["entry.vi"]


def test_with_cte_is_allowed(tmp_path: Path):
    root = _project(tmp_path)
    res = sql.run_query(
        root,
        "WITH errs AS (SELECT name FROM terminal WHERE type_descriptor = 'Error') "
        "SELECT COUNT(*) FROM errs",
    )
    # 4 error clusters total: 2 'error out' + 1 input 'error in' (a.vi) + 'err'
    # (b.vi). This CTE filters on type_descriptor only, not direction.
    assert res.rows == [[4]]


# === Sandbox: structural read-only enforcement =============================


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DELETE FROM vis",
        "INSERT INTO vis(path, name) VALUES ('x', 'y')",
        "UPDATE vis SET name = 'z'",
        "DROP TABLE vis",
        "PRAGMA table_list",
        "ATTACH DATABASE 'evil.db' AS evil",
        "CREATE TABLE t(x)",
        "   ",  # empty
    ],
)
def test_non_select_rejected(tmp_path: Path, bad_sql: str):
    root = _project(tmp_path)
    with pytest.raises(sql.QueryError):
        sql.run_query(root, bad_sql)


def test_stacked_statement_rejected(tmp_path: Path):
    """A second statement smuggled after a SELECT must be refused (sqlite3
    raises Warning, not Error — regression guard for that catch)."""
    root = _project(tmp_path)
    with pytest.raises(sql.QueryError):
        sql.run_query(root, "SELECT 1; DROP TABLE vis")


def test_rejected_write_leaves_db_untouched(tmp_path: Path):
    """Proof the connection is genuinely read-only: a write attempt cannot
    change the stored bytes."""
    root = _project(tmp_path)
    db = sql.store.db_path(root)
    before = db.read_bytes()
    with pytest.raises(sql.QueryError):
        sql.run_query(root, "DELETE FROM vis")
    # even a keyword-guard-bypassing attempt can't write: the file is ro.
    assert db.read_bytes() == before
    # and the data is still all there
    assert sql.run_query(root, "SELECT COUNT(*) FROM vi").rows == [[2]]


def test_leading_comment_then_write_rejected(tmp_path: Path):
    root = _project(tmp_path)
    with pytest.raises(sql.QueryError):
        sql.run_query(root, "-- harmless\nDELETE FROM vis")


def test_leading_comment_then_select_allowed(tmp_path: Path):
    root = _project(tmp_path)
    res = sql.run_query(root, "/* count */ SELECT COUNT(*) FROM vi")
    assert res.rows == [[2]]


# === Limits: row cap, timeout, unindexed ===================================


def test_row_cap_truncates(tmp_path: Path):
    facts = [
        VIFacts(path=str(tmp_path / f"v{i}.vi"), name=f"v{i}.vi", content_sha=str(i))
        for i in range(10)
    ]
    save_index(tmp_path, facts)
    res = sql.run_query(tmp_path, "SELECT path FROM vi", row_cap=4)
    assert res.truncated is True
    assert res.row_count == 4


def test_timeout_aborts_runaway_recursion(tmp_path: Path):
    root = _project(tmp_path)
    # COUNT(*) forces full materialization of a huge recursion before any row
    # is produced, so the progress-handler deadline fires (a streaming
    # `SELECT x FROM c` would instead return the first rows lazily and never
    # trip the timer).
    with pytest.raises(sql.QueryError, match="time limit"):
        sql.run_query(
            root,
            "WITH RECURSIVE c(x) AS ("
            "  SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 1000000000"
            ") SELECT COUNT(*) FROM c",
            timeout_s=0.2,
        )


def test_unindexed_project_is_loud(tmp_path: Path):
    with pytest.raises(sql.QueryError, match="no index"):
        sql.run_query(tmp_path / "never-indexed", "SELECT * FROM vi")


# === Acceptance: SQL over a REAL built index == the Python-filter answer ====
#
# The synthetic tests prove the SQL machinery; these prove the VIEW layer maps
# onto real, freshly-built facts — the driving question answered over an index
# that store.save() actually wrote (design §7 acceptance).

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_JKI_ROOT = _SAMPLES / "JKI-VI-Tester"
_TESTCASE_DIR = _JKI_ROOT / "source" / "Classes" / "TestCase"


@pytest.mark.needs_samples
def test_histogram_over_real_facts_matches_independent_count():
    """On a real class dir: the SQL error-indicator histogram equals an
    independent Counter over the same facts — the view layer maps onto real,
    store.save()-written rows (not just the synthetic ones)."""
    from collections import Counter

    from lvkit.index.build import build_index
    from lvkit.index.project import resolve_project

    root, vi_paths = resolve_project(_TESTCASE_DIR)
    facts = build_index(root, vi_paths).facts
    save_index(root, facts)

    sql_res = sql.error_indicator_histogram(root)
    sql_hist = {name: n for name, n in sql_res.rows}

    expected = dict(
        Counter(
            t.name
            for f in facts
            for t in f.terminals
            if t.type_descriptor == "Error" and t.direction == "output"
        )
    )

    assert sql_hist == expected


@pytest.mark.needs_samples
@pytest.mark.slow
def test_jki_error_indicator_histogram_16_rows():
    """The full design-doc acceptance: over JKI VI Tester the histogram is a
    small table dominated by 'error out' — the answer, not the 406 raw rows."""
    if not (_JKI_ROOT.is_dir() and any(_JKI_ROOT.rglob("*.vi"))):
        pytest.skip("JKI-VI-Tester sample not present")

    import os

    from lvkit.index.build import build_index
    from lvkit.index.project import resolve_project

    # Reuse the developer's warm extraction cache (see test_index.jki_index).
    saved = os.environ.pop("LVKIT_CACHE_DIR", None)
    try:
        root, vi_paths = resolve_project(_JKI_ROOT)
        facts = build_index(root, vi_paths).facts
        save_index(root, facts)
        res = sql.error_indicator_histogram(root)
    finally:
        if saved is not None:
            os.environ["LVKIT_CACHE_DIR"] = saved

    assert res.columns == ["name", "n"]
    assert res.rows  # non-empty histogram
    top_name, top_count = res.rows[0]
    assert top_name == "error out"
    assert top_count == 382
    # It's a small histogram, not a row dump — exactly 16 distinct names.
    assert len(res.rows) == 16
    assert not res.truncated
