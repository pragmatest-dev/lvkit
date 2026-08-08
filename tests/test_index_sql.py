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
from lvkit.index.model import (
    OUTPUT,
    WIRED_INDICATOR,
    ConstantFact,
    TerminalFact,
    VIFacts,
)
from lvkit.index.store import save as save_index


def _term(name: str, *, direction: str = OUTPUT, error: bool = False) -> TerminalFact:
    return TerminalFact(
        name=name,
        direction=direction,
        is_indicator=(direction == OUTPUT),
        is_public=True,
        control_type=None,
        py_type="object",
        is_error_cluster=error,
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


# === Schema introspection ==================================================


def test_describe_schema_lists_all_views():
    views = {v.name for v in sql.describe_schema()}
    assert views == {"vi", "terminal", "constant", "call", "type_use", "class_fact"}


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
