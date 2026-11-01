"""MCP tools return their result plus the problems found with the VIs they used;
a call fails only for a bad argument, and no call sees another call's problems."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest

from lvkit.mcp import server as srv

GOOD_VI = Path(__file__).parent / "corpus" / "issues" / "132" / "Run.vi"
TWO_LIBS = Path(__file__).parent / "corpus" / "issues" / "29" / "Test LVKit"


def _run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copy(GOOD_VI, root / "Good.vi")
    (root / "Bad.vi").write_text("not a vi", encoding="utf-8")
    return root


def test_read_vi_of_a_good_vi_is_complete(project: Path) -> None:
    result = _run(srv.read_vi(str(project / "Good.vi")))
    assert (result["status"], result["problems"]) == ("Complete", [])
    assert "body" in result and "inputs" in result


def test_read_vi_of_an_unreadable_vi_answers_stopped_not_an_error(
    project: Path,
) -> None:
    result = _run(srv.read_vi(str(project / "Bad.vi")))
    assert result["status"] == "Stopped"
    (problem,) = result["problems"]
    assert problem["vi"] == "Bad.vi" and problem["kind"] == "unreadable"
    assert "body" not in result


def test_a_missing_file_is_still_a_bad_argument(project: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _run(srv.read_vi(str(project / "Nope.vi")))


def test_an_unreadable_subvi_comes_with_the_result_and_is_not_remembered(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Test LVKit"
    shutil.copytree(TWO_LIBS, root)
    caller = root / "Lib2" / "Class" / "Test.vi"
    (root / "Lib2" / "Class" / "Do.vi").write_text("not a vi", encoding="utf-8")
    first = _run(srv.read_vi(str(caller)))
    assert first["status"] == "Partial"
    assert [p["vi"] for p in first["problems"]] == ["Lib2/Class/Do.vi"]
    fixed = Path("Lib2") / "Class" / "Do.vi"
    shutil.copy(TWO_LIBS / fixed, root / fixed)
    second = _run(srv.read_vi(str(caller)))
    assert (second["status"], second["problems"]) == ("Complete", [])


def test_index_returns_the_problem_list_and_still_indexes_the_rest(
    project: Path,
) -> None:
    result = _run(srv.index(str(project)))
    assert result["vis"] == 1
    assert result["status"] == "Partial"
    assert [p["vi"] for p in result["problems"]] == ["Bad.vi"]


def test_query_reports_the_vis_the_index_is_missing(project: Path) -> None:
    result = _run(srv.query("SELECT name FROM vi", project=str(project)))
    assert result["row_count"] == 1
    assert result["status"] == "Partial"
    assert [p["vi"] for p in result["problems"]] == ["Bad.vi"]


def test_render_of_an_unreadable_vi_answers_stopped(project: Path) -> None:
    result = _run(srv.render(str(project / "Bad.vi")))
    assert result["status"] == "Stopped" and "render_path" not in result


def test_unresolved_returns_items_and_problems(project: Path) -> None:
    result = _run(srv.unresolved(str(project)))
    assert isinstance(result["items"], list)
    assert result["status"] == "Partial"
    assert [p["vi"] for p in result["problems"]] == ["Bad.vi"]
