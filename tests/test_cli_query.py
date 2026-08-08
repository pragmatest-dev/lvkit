"""Tests for the `lvkit query` CLI subcommand (cmd_query).

Driven in-process against a synthetic index saved to the hermetic per-test
cache — no subprocess, no sample corpus.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from lvkit.cli import cmd_query
from lvkit.index.model import OUTPUT, TerminalFact, VIFacts
from lvkit.index.store import save as save_index


def _args(input_path: Path, sql: str | None = None, *, schema: bool = False,
          fmt: str = "table") -> argparse.Namespace:
    return argparse.Namespace(
        input_path=str(input_path), sql=sql, schema=schema, format=fmt
    )


def _seed(root: Path) -> None:
    save_index(root, [
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
    ])


def test_table_histogram(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_query(_args(
        tmp_path,
        "SELECT name, COUNT(*) AS n FROM terminal WHERE is_error_cluster=1 "
        "GROUP BY name ORDER BY n DESC, name",
    ))
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
        "columns": ["n"], "rows": [[3]], "row_count": 1, "truncated": False,
    }


def test_schema_lists_views(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_query(_args(tmp_path, schema=True))
    out = capsys.readouterr().out
    assert rc == 0
    for view in ("vi", "terminal", "constant", "call", "type_use", "class_fact"):
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


def test_unindexed_project_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    rc = cmd_query(_args(tmp_path / "empty", "SELECT * FROM vi"))
    assert rc == 2
    assert "no index" in capsys.readouterr().err
