"""Tests for the `lvkit query` CLI subcommand (cmd_query).

Driven in-process against a synthetic index saved to the hermetic per-test
cache — no subprocess. Most tests seed a synthetic index and pass
``no_refresh=True`` to exercise the pure query path; one ``needs_samples`` test
covers the default build-on-first-query (refresh) path against a real class dir.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from lvkit.cli import cmd_query
from lvkit.index.model import OUTPUT, TerminalFact, VIFacts
from lvkit.index.store import save as save_index


def _args(
    input_path: Path,
    sql: str | None = None,
    *,
    schema: bool = False,
    fmt: str = "table",
    no_refresh: bool = True,
) -> argparse.Namespace:
    # These tests seed a synthetic index (save_index of hand-built facts) with
    # no real .vi files behind it, so they default to no_refresh=True: they
    # exercise the QUERY path over a known index, not the build/refresh path
    # (which would rebuild from the [nonexistent] real files and wipe the seed).
    # The refresh default is covered separately by test_default_refreshes_index.
    return argparse.Namespace(
        input_path=str(input_path),
        sql=sql,
        schema=schema,
        format=fmt,
        no_refresh=no_refresh,
    )


def _seed(root: Path) -> None:
    save_index(
        root,
        [
            VIFacts(
                path=str(root / "a.vi"),
                name="a.vi",
                content_sha="a",
                terminals=[
                    TerminalFact("error out", OUTPUT, True, True, None, "obj", True),
                    TerminalFact("error out", OUTPUT, True, True, None, "obj", True),
                    TerminalFact("err", OUTPUT, True, True, None, "obj", True),
                ],
            ),
        ],
    )


def test_table_histogram(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_query(
        _args(
            tmp_path,
            "SELECT name, COUNT(*) AS n FROM terminal WHERE is_error_cluster=1 "
            "GROUP BY name ORDER BY n DESC, name",
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "name" in out and "n" in out
    assert "error out" in out
    # header + separator + 2 data rows
    assert out.strip().count("\n") == 3


def test_json_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_query(_args(tmp_path, "SELECT COUNT(*) AS n FROM terminal", fmt="json"))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "columns": ["n"],
        "rows": [[3]],
        "row_count": 1,
        "truncated": False,
    }


def test_schema_lists_views(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_query(_args(tmp_path, schema=True))
    out = capsys.readouterr().out
    assert rc == 0
    for view in (
        "vi",
        "terminal",
        "constant",
        "call",
        "type_use",
        "class_fact",
        "lvproj",
    ):
        assert view in out


def test_rejected_write_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_query(_args(tmp_path, "DELETE FROM vis"))
    assert rc == 2
    assert "query error" in capsys.readouterr().err


def test_missing_sql_without_schema_exits_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    _seed(tmp_path)
    rc = cmd_query(_args(tmp_path, None))
    assert rc == 2
    assert "--schema" in capsys.readouterr().err


def test_unindexed_project_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    rc = cmd_query(_args(tmp_path / "empty", "SELECT * FROM vi"))
    assert rc == 2
    assert "no index" in capsys.readouterr().err


_TESTCASE_DIR = (
    Path(__file__).resolve().parent.parent
    / ".lvkit"
    / "cache"
    / "samples"
    / "JKI-VI-Tester"
    / "source"
    / "Classes"
    / "TestCase"
)


@pytest.mark.needs_samples
def test_default_refreshes_index(capsys: pytest.CaptureFixture[str]):
    """The default (no --no-refresh) builds/refreshes the index before reading,
    so a query works on a never-indexed project without a separate `lvkit
    index` step and reflects the current files."""
    if not _TESTCASE_DIR.exists():
        pytest.skip("sample class not available")
    # no_refresh=False -> ensure_fresh_index builds the cold index on first query
    rc = cmd_query(
        _args(
            _TESTCASE_DIR, "SELECT COUNT(*) AS n FROM vi", fmt="json", no_refresh=False
        )
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"][0][0] > 0  # real VIs got indexed + counted
