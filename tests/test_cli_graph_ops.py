"""Tests for the CLI call-graph ops (cmd_graph_op): callers/callees/blast-radius.

The CLI twin of the MCP get_callers/get_callees/blast_radius tools. Driven
in-process against a synthetic index (a → b call edge) saved to the hermetic
per-test cache; no subprocess, no sample corpus.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from lvkit.cli import cmd_graph_op
from lvkit.index.model import VIFacts
from lvkit.index.store import save as save_index


def _args(command: str, vi: Path, project: Path, *, fmt: str = "table",
          depth: int | None = None, no_refresh: bool = True) -> argparse.Namespace:
    # no_refresh=True: synthetic facts with no real .vi files behind them, so
    # skip the build/refresh (which would rebuild from nothing and wipe the seed).
    return argparse.Namespace(
        command=command, vi=str(vi), project=str(project),
        format=fmt, depth=depth, no_refresh=no_refresh,
    )


def _seed(root: Path) -> None:
    """a.vi calls b.vi (via b's qualified name)."""
    save_index(root, [
        VIFacts(path=str(root / "a.vi"), name="a.vi",
                qualified_name="Lib:a.vi", content_sha="a", calls=["Lib:b.vi"]),
        VIFacts(path=str(root / "b.vi"), name="b.vi",
                qualified_name="Lib:b.vi", content_sha="b"),
    ])


def test_callers(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_graph_op(_args("callers", tmp_path / "b.vi", tmp_path, fmt="json"))
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == [str(tmp_path / "a.vi")]


def test_callees(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_graph_op(_args("callees", tmp_path / "a.vi", tmp_path, fmt="json"))
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == [str(tmp_path / "b.vi")]


def test_blast_radius_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_graph_op(_args("blast-radius", tmp_path / "b.vi", tmp_path, fmt="json"))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["impact_score"] == 1
    assert str(tmp_path / "a.vi") in payload["dependents"]


def test_blast_radius_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    _seed(tmp_path)
    rc = cmd_graph_op(_args("blast-radius", tmp_path / "b.vi", tmp_path))
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 transitive dependent" in out
    assert "a.vi" in out


def test_no_index_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    rc = cmd_graph_op(_args("callers", tmp_path / "x.vi", tmp_path / "empty"))
    assert rc == 2
    assert "no index" in capsys.readouterr().err
