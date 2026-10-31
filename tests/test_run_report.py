"""The one problem-reporting rule for the load path and the index commands: a VI
that cannot be read is recorded and the run goes on (``run_report``).

Driven in-process on a tiny project: one real VI from the checked-in corpus and
one file that is not a VI.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import pytest

from lvkit import extractor, run_report
from lvkit.cli import cmd_graph_op, cmd_index, cmd_query
from lvkit.cli_run import run_command
from lvkit.graph import InMemoryVIGraph, LoadMode
from lvkit.index import build as index_build
from lvkit.run_report import (
    EXIT_COMPLETE,
    EXIT_PARTIAL,
    EXIT_STOPPED,
    Outcome,
    Problem,
    ProblemKind,
    ProblemStage,
    RunReport,
    RunSettings,
    RunStopped,
)

GOOD_VI = Path(__file__).parent / "corpus" / "issues" / "132" / "Run.vi"


def _project(tmp_path: Path, *, bad: bool = True) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copy(GOOD_VI, root / "Good.vi")
    if bad:
        (root / "Bad.vi").write_text("not a vi", encoding="utf-8")
    return root


def _index_args(root: Path, **extra: object) -> argparse.Namespace:
    return argparse.Namespace(input_path=str(root), refresh=False, **extra)


def _problem(vi: str = "A.vi", reason: str = "boom") -> Problem:
    return Problem(vi, ProblemStage.LOAD, ProblemKind.UNREADABLE, reason)


# ---- the report itself --------------------------------------------------------


def test_a_problem_is_data_on_its_vi_s_node_and_prints_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    graph = InMemoryVIGraph(RunSettings(echo=True))
    graph.record_problem("/p/A.vi", _problem())
    graph.record_problem("/p/A.vi", _problem())  # found again: kept once
    assert graph.problems(["/p/A.vi"]) == [_problem()]
    assert graph.problems(["/p/B.vi"]) == []
    assert capsys.readouterr().err == "warning: A.vi: load: boom\n"


def test_a_successful_load_clears_the_problems_of_the_vi(tmp_path: Path) -> None:
    root = _project(tmp_path, bad=False)
    graph = InMemoryVIGraph()
    key = str((root / "Good.vi").resolve())
    graph.record_problem(key, _problem("Good.vi"))
    graph.load_vi(root / "Good.vi", LoadMode.MINIMAL)
    assert graph.problems([key]) == []


def test_summary_lists_every_problem_and_exit_codes() -> None:
    assert RunReport.of([]).outcome() is Outcome.COMPLETE
    assert RunReport.of([]).exit_code() == EXIT_COMPLETE
    report = RunReport.of(_problem(f"V{n}.vi") for n in range(25))
    lines = report.summary_lines()
    assert lines[0] == "Partial: 25 problem(s)"
    assert len([ln for ln in lines if ln.strip().startswith("warning:")]) == 25
    assert report.exit_code() == EXIT_PARTIAL
    assert report.exit_code(stopped=True) == EXIT_STOPPED
    assert report.to_json()["status"] == "Partial"
    assert len(report.to_json()["problems"]) == 25  # type: ignore[arg-type]


def test_fail_fast_stops_at_the_first_problem() -> None:
    graph = InMemoryVIGraph(RunSettings(fail_fast=True))
    with pytest.raises(RunStopped) as stop:
        graph.record_problem("/p/A.vi", _problem())
    assert stop.value.problem.vi == "A.vi"
    assert not isinstance(stop.value, Exception)


def test_an_lvkit_error_exits_stopped_with_a_hint() -> None:
    report = RunReport.of(
        [run_report.problem_for_exception("A.vi", ProblemStage.INDEX, KeyError("x"))]
    )
    (problem,) = report.problems
    assert problem.kind is ProblemKind.LVKIT_ERROR
    assert problem.hint is not None and "report it" in problem.hint
    assert report.exit_code() == EXIT_STOPPED


def test_label_is_never_absolute(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / ".git").mkdir()
    vi = root / "sub" / "X.vi"
    vi.write_text("", encoding="utf-8")
    vilib = tmp_path / "vi.lib"
    (vilib / "Utility").mkdir(parents=True)
    lib_vi = vilib / "Utility" / "Y.vi"
    lib_vi.write_text("", encoding="utf-8")
    settings = RunSettings(library_roots={"<vilib>": vilib})
    assert settings.label(vi) == "sub/X.vi"
    assert settings.label(lib_vi) == "<vilib>/Utility/Y.vi"
    assert settings.label(root) == "proj"  # the project folder itself, never "."
    assert not Path(settings.label(tmp_path / "elsewhere" / "Z.vi")).is_absolute()


# ---- the load path ------------------------------------------------------------


def test_load_directory_records_an_unreadable_vi_and_loads_the_rest(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    graph = InMemoryVIGraph()
    graph.load_directory(root, LoadMode.MINIMAL)
    assert len(graph.list_vis()) == 1
    (problem,) = graph.problems()
    assert (problem.vi, problem.stage, problem.kind) == (
        "Bad.vi",
        ProblemStage.EXTRACT,
        ProblemKind.UNREADABLE,
    )


def test_unreadable_llb_is_recorded_not_skipped_silently(tmp_path: Path) -> None:
    llb = tmp_path / "Lib.llb"
    llb.write_text("not an llb", encoding="utf-8")
    graph = InMemoryVIGraph()
    graph.load_llb(llb, LoadMode.MINIMAL)
    (problem,) = graph.problems()
    assert problem.vi == "Lib.llb"


def test_a_block_diagram_that_cannot_be_exported_carries_pylabview_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_block(vi_path: Path, output_dir: Path, artifact_stem: str) -> None:
        print(
            f"{vi_path}: Warning: Block b'BDHc' section 0 XML export exception: x.",
            file=sys.stderr,
        )

    monkeypatch.setattr(extractor, "_extract_in_process", fail_block)
    root = _project(tmp_path, bad=False)
    graph = InMemoryVIGraph()
    graph.load_directory(root, LoadMode.MINIMAL)
    (problem,) = graph.problems()
    assert problem.reason == "no block diagram could be read"
    assert problem.detail == (
        "Good.vi: Warning: Block b'BDHc' section 0 XML export exception: x.",
    )
    assert str(tmp_path) not in "".join(problem.lines())


# ---- index --------------------------------------------------------------------


def test_index_completes_reports_the_unreadable_vi_and_exits_partial(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    rc = cmd_index(_index_args(root))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == EXIT_PARTIAL
    assert payload["vis"] == 1
    assert payload["loaded_alone"] == 0
    assert payload["status"] == "Partial"
    assert [p["vi"] for p in payload["problems"]] == ["Bad.vi"]
    assert captured.err.count("warning: Bad.vi: extract:") == 2  # line + summary
    assert "Partial: 1 problem(s)" in captured.err
    assert str(tmp_path) not in captured.err


def test_index_of_a_clean_project_is_complete(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = cmd_index(_index_args(_project(tmp_path, bad=False)))
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_COMPLETE
    assert (payload["status"], payload["problems"]) == ("Complete", [])


def test_index_fail_fast_stops_at_the_first_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = run_command(cmd_index, _index_args(_project(tmp_path), fail_fast=True))
    captured = capsys.readouterr()
    assert rc == EXIT_STOPPED
    assert json.loads(captured.out)["status"] == "Stopped"
    assert "Stopped: 1 problem(s)" in captured.err


def test_an_lvkit_error_in_one_vi_is_recorded_and_the_rest_is_indexed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path, bad=False)
    shutil.copy(GOOD_VI, root / "Second.vi")
    real = index_build.project_vi_facts

    def flaky(graph, vi_name, vi_path, project_root):  # type: ignore[no-untyped-def]
        if vi_path.name == "Second.vi":
            raise KeyError("boom")
        return real(graph, vi_name, vi_path, project_root)

    monkeypatch.setattr(index_build, "project_vi_facts", flaky)
    rc = cmd_index(_index_args(root))
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_STOPPED
    assert payload["vis"] == 1
    (problem,) = payload["problems"]
    assert problem["kind"] == "lvkit error" and "report it" in problem["hint"]


def test_refresh_drops_the_stale_row_of_a_vi_that_became_unreadable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, bad=False)
    assert cmd_index(_index_args(root)) == EXIT_COMPLETE
    capsys.readouterr()
    (root / "Good.vi").write_text("now broken", encoding="utf-8")
    args = _index_args(root)
    args.refresh = True
    rc = cmd_index(args)
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_PARTIAL
    assert payload["total"] == 0 and payload["rebuilt"] == 0


# ---- empty answers say why -----------------------------------------------------


def _graph_op_args(command: str, vi: str, root: Path) -> argparse.Namespace:
    return argparse.Namespace(
        command=command,
        vi=vi,
        project=str(root),
        format="json",
        depth=None,
        no_refresh=False,
    )


def test_an_unknown_vi_is_an_error_not_an_empty_answer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, bad=False)
    for command in ("callers", "callees", "blast-radius"):
        rc = cmd_graph_op(_graph_op_args(command, "Nope.vi", root))
        captured = capsys.readouterr()
        assert rc == EXIT_STOPPED
        assert captured.out == ""
        assert "'Nope.vi' was not found in the index" in captured.err


def test_a_known_vi_with_no_callers_says_so_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, bad=False)
    rc = cmd_graph_op(_graph_op_args("callers", str(root / "Good.vi"), root))
    captured = capsys.readouterr()
    assert rc == EXIT_COMPLETE
    assert json.loads(captured.out) == []
    assert "has no callers in the index" in captured.err


def test_query_with_no_rows_says_so_and_reports_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    args = argparse.Namespace(
        input_path=str(root),
        sql="SELECT name FROM vi WHERE name = 'none'",
        schema=False,
        format="json",
        no_refresh=False,
    )
    rc = cmd_query(args)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == EXIT_PARTIAL
    assert payload["rows"] == [] and payload["status"] == "Partial"
    assert "the query returned no rows" in captured.err


def test_a_cache_write_failure_is_an_environment_problem_not_a_bug(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(extractor, "_publish", disk_full)
    root = _project(tmp_path, bad=False)
    rc = cmd_index(_index_args(root))
    payload = json.loads(capsys.readouterr().out)
    (problem,) = payload["problems"]
    assert (problem["kind"], problem["stage"]) == ("environment", "cache")
    assert "could not write the extraction cache at" in problem["reason"]
    assert "No space left on device" in problem["reason"]
    assert problem["hint"] is None
    assert str(Path.home()) not in problem["reason"]
    assert rc == EXIT_STOPPED
