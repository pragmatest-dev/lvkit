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
    WIRED_INDICATOR,
    ConstantFact,
    TerminalFact,
    VIFacts,
)
from lvkit.index.store import load as load_index
from lvkit.index.store import save as save_index


def _term(
    name: str,
    *,
    direction: str = OUTPUT,
    error: bool = False,
    lv_type: str = "?",
    enum_values: list[str] | None = None,
) -> TerminalFact:
    return TerminalFact(
        name=name,
        direction=direction,
        is_indicator=(direction == OUTPUT),
        is_public=True,
        control_type=None,
        py_type="object",
        is_error_cluster=error,
        lv_type=lv_type,
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
                ConstantFact(value="42", label="answer", py_type="int",
                             wired_to=WIRED_INDICATOR),
            ],
            calls=["Lib.lvlib:b.vi"],
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
        capture_output=True, text=True, check=True,
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
        "vi", "terminal", "constant", "call", "type_use", "class_fact", "lvproj",
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
        "WHERE is_error_cluster = 1 AND direction = 'output' "
        "GROUP BY name ORDER BY n DESC, name",
    )
    assert res.rows == [["error out", 2], ["err", 1]]


def test_constants_wired_to_indicator(tmp_path: Path):
    root = _project(tmp_path)
    res = sql.run_query(
        root, "SELECT value, label FROM constant WHERE wired_to = 'indicator'"
    )
    assert res.rows == [["42", "answer"]]


def test_lv_type_and_enum_values_round_trip(tmp_path: Path):
    """Schema v3 (#22): the faithful ``lv_type`` label and ordinal
    ``enum_values`` round-trip through save/load and are queryable via SQL —
    not just ``py_type``'s lossy codegen projection."""
    facts = [
        VIFacts(
            path=str(tmp_path / "m.vi"),
            name="m.vi",
            content_sha="sha-m",
            terminals=[
                _term(
                    "method", direction="input",
                    lv_type="MethodEnum{setUp, testMethod, tearDown}",
                    enum_values=["setUp", "testMethod", "tearDown"],
                ),
                _term("result"),  # non-enum terminal keeps the defaults
            ],
        ),
    ]
    save_index(tmp_path, facts)

    res = sql.run_query(
        tmp_path,
        "SELECT name, lv_type, enum_values FROM terminal ORDER BY name",
    )
    assert res.columns == ["name", "lv_type", "enum_values"]
    rows = {r[0]: (r[1], r[2]) for r in res.rows}
    assert rows["method"] == (
        "MethodEnum{setUp, testMethod, tearDown}",
        '["setUp", "testMethod", "tearDown"]',
    )
    assert rows["result"] == ("?", "[]")

    # Queryable: find terminals whose enum carries a given member.
    hits = sql.run_query(
        tmp_path,
        "SELECT name FROM terminal WHERE enum_values LIKE '%\"testMethod\"%'",
    )
    assert hits.rows == [["method"]]


def test_constant_lv_type_is_faithful_not_lossy_py_type(tmp_path: Path):
    """A constant carries a FAITHFUL ``lv_type`` label alongside the lossy
    ``py_type`` codegen projection (#8), same discipline as terminals."""
    facts = [
        VIFacts(
            path=str(tmp_path / "k.vi"),
            name="k.vi",
            content_sha="sha-k",
            constants=[
                ConstantFact(
                    value="0", label="err", py_type="dict[str, Any]",
                    lv_type="error cluster",
                ),
                ConstantFact(value="3", label=None, py_type="int", lv_type="I32"),
            ],
        ),
    ]
    save_index(tmp_path, facts)
    res = sql.run_query(
        tmp_path, "SELECT value, py_type, lv_type FROM constant ORDER BY value"
    )
    rows = {r[0]: (r[1], r[2]) for r in res.rows}
    assert rows["0"] == ("dict[str, Any]", "error cluster")
    assert rows["3"] == ("int", "I32")


def test_class_private_data_round_trips(tmp_path: Path):
    """A class method VI records its owning class's private-data fields (#8),
    faithfully rendered 'name: <lv_type>', round-tripped and queryable."""
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
    res = sql.run_query(
        tmp_path, "SELECT owning_class, private_data FROM class_fact"
    )
    assert res.rows == [[
        "TestCase.lvclass",
        '["testMethodName: String", "isSkipped: TF"]',
    ]]


def test_uncalled_column_flags_dead_code(tmp_path: Path):
    """#20: dead-code detection is a ``callers_count = 0`` filter on the ``vi``
    view (a real, store-round-tripped column), NOT a fragile
    ``qualified_name``/``callee_key`` name anti-join. The column persists and
    is queryable; ``callers_count = 0`` selects exactly the uncalled VIs even
    when ``qualified_name`` is NULL."""
    facts = [
        VIFacts(
            path=str(tmp_path / "entry.vi"), name="entry.vi",
            qualified_name=None, content_sha="s1", callers_count=0,
        ),
        VIFacts(
            path=str(tmp_path / "sub.vi"), name="sub.vi",
            qualified_name=None, content_sha="s2", callers_count=3,
        ),
    ]
    save_index(tmp_path, facts)

    dead = sql.run_query(tmp_path, "SELECT name FROM vi WHERE callers_count = 0")
    assert dead.rows == [["entry.vi"]]

    # The column round-trips (value, not just the DEFAULT).
    both = sql.run_query(
        tmp_path, "SELECT name, callers_count FROM vi ORDER BY name"
    )
    assert both.rows == [["entry.vi", 0], ["sub.vi", 3]]

    # The canned helper is the same filter.
    canned = sql.uncalled_vis(tmp_path)
    assert canned.columns == ["path", "name", "library"]
    assert [r[1] for r in canned.rows] == ["entry.vi"]


def test_with_cte_is_allowed(tmp_path: Path):
    root = _project(tmp_path)
    res = sql.run_query(
        root,
        "WITH errs AS (SELECT name FROM terminal WHERE is_error_cluster = 1) "
        "SELECT COUNT(*) FROM errs",
    )
    # 4 error clusters total: 2 'error out' + 1 input 'error in' (a.vi) + 'err'
    # (b.vi). This CTE filters on is_error_cluster only, not direction.
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

    expected = dict(Counter(
        t.name
        for f in facts
        for t in f.terminals
        if t.is_error_cluster and t.direction == "output"
    ))

    assert sql_hist == expected


@pytest.mark.needs_samples
@pytest.mark.slow
def test_jki_error_indicator_histogram_13_rows():
    """The full design-doc acceptance: over JKI VI Tester the histogram is a
    small table dominated by 'error out' — the answer, not the 379 raw rows."""
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
    assert top_count >= 298
    # It's a small histogram (a handful of distinct names), not a row dump.
    assert len(res.rows) < 30
    assert not res.truncated
