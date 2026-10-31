"""The rule's edges: fail-fast in every command, an unreadable control reported on
every sync, one stop function for the command line and MCP, every "declined" or
"Error:" path as a recorded problem, page names that stay unique, and the labels
and node types problems carry."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit import cli
from lvkit.cli import cmd_index, cmd_query
from lvkit.docs.generate import _collect_library_vis
from lvkit.docs.page_names import page_names
from lvkit.graph import InMemoryVIGraph, LoadMode
from lvkit.graph.node_kinds import NodeType
from lvkit.mcp import server as srv
from lvkit.render import render_vi_file_report
from lvkit.run_report import (
    EXIT_PARTIAL,
    EXIT_STOPPED,
    Problem,
    ProblemKind,
    ProblemStage,
    RunProblems,
    RunReport,
    RunSettings,
    UnreadableVIError,
)
from lvkit.stderr_capture import capture_stdout

GOOD_VI = Path(__file__).parent / "corpus" / "issues" / "132" / "Run.vi"
TWO_LIBS = Path(__file__).parent / "corpus" / "issues" / "29" / "Test LVKit"


def _lvkit(monkeypatch: pytest.MonkeyPatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["lvkit", *argv])
    return cli.main()


def _with_unreadable_subvi(tmp_path: Path) -> Path:
    """The two-library project with Lib2's ``Do.vi`` broken; returns its caller."""
    root = tmp_path / "Test LVKit"
    shutil.copytree(TWO_LIBS, root)
    (root / "Lib2" / "Class" / "Do.vi").write_text("not a vi", encoding="utf-8")
    return root / "Lib2" / "Class" / "Test.vi"


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copy(GOOD_VI, root / "Good.vi")
    (root / "Bad.vi").write_text("not a vi", encoding="utf-8")
    return root


# ---- H1: one stop wrapper for every command --------------------------------------


def test_render_fail_fast_stops_on_an_unreadable_subvi_on_a_miss_and_a_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    caller = _with_unreadable_subvi(tmp_path)
    code = _lvkit(monkeypatch, "render", str(caller), "--format", "svg", "--fail-fast")
    err = capsys.readouterr().err
    assert code == 1 and "Stopped: 1 problem(s)" in err and "Traceback" not in err

    # Fill the cache without --fail-fast (exit 3), then ask again: a hit.
    assert _lvkit(monkeypatch, "render", str(caller), "--format", "svg") == 3
    capsys.readouterr()

    def no_build(*args: object, **kwargs: object) -> None:
        raise AssertionError("rebuilt on a cache hit")

    monkeypatch.setattr("lvkit.render.body.render_body_with_name", no_build)
    code = _lvkit(monkeypatch, "render", str(caller), "--format", "svg", "--fail-fast")
    err = capsys.readouterr().err
    assert code == 1 and "Stopped: 1 problem(s)" in err and "Traceback" not in err


def test_render_directory_fail_fast_stops_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    caller = _with_unreadable_subvi(tmp_path)
    code = _lvkit(monkeypatch, "render", str(caller.parent), "--fail-fast")
    err = capsys.readouterr().err
    assert code == 1 and "Stopped: 1 problem(s)" in err and "Traceback" not in err


# ---- H2: an unreadable control is reported on every sync --------------------------


def test_index_reports_an_unreadable_control_on_every_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    (root / "Bad.vi").unlink()
    (root / "Bad.ctl").write_text("not a control", encoding="utf-8")
    for refresh in (False, True, True):
        args = argparse.Namespace(input_path=str(root), refresh=refresh)
        assert cmd_index(args) == EXIT_PARTIAL
        payload = json.loads(capsys.readouterr().out)
        assert [p["vi"] for p in payload["problems"]] == ["Bad.ctl"]


# ---- H3: one stop function --------------------------------------------------------


def test_fail_names_the_file_the_error_names_not_the_one_asked_about(
    tmp_path: Path,
) -> None:
    run = RunProblems(RunSettings(root=tmp_path))
    bad = tmp_path / "Bad.vi"
    run.fail(tmp_path / "Good.vi", ProblemStage.DIFF, UnreadableVIError("no", path=bad))
    (problem,) = run.report().problems
    assert problem.vi == "Bad.vi"


def test_mcp_diff_blames_the_unreadable_after_side(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = asyncio.run(srv.diff(str(root / "Good.vi"), str(root / "Bad.vi")))
    assert result["status"] == "Stopped"
    assert [p["vi"] for p in result["problems"]] == ["Bad.vi"]


def test_mcp_unresolved_on_one_unreadable_vi_answers_stopped(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = asyncio.run(srv.unresolved(str(root / "Bad.vi")))
    assert result["status"] == "Stopped"
    assert [p["vi"] for p in result["problems"]] == ["Bad.vi"]


# ---- M1: every leftover is a recorded problem -------------------------------------


def test_a_declined_render_and_diff_are_problems_not_bare_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vi = _project(tmp_path) / "Good.vi"
    monkeypatch.setattr("lvkit.render.body.render_body_with_name", lambda *a, **k: None)
    monkeypatch.setattr("lvkit.vi_diff.diff_vi_files", lambda *a, **k: None)
    assert _lvkit(monkeypatch, "render", str(vi)) == 1
    err = capsys.readouterr().err
    assert "warning: Good.vi: render: nothing to draw" in err
    assert "Stopped: 1 problem(s)" in err
    assert _lvkit(monkeypatch, "diff", str(vi), str(vi)) == 1
    assert "warning: Good.vi: diff: nothing to draw" in capsys.readouterr().err
    mcp_render = asyncio.run(srv.render(str(vi)))
    assert mcp_render["status"] == "Stopped" and mcp_render["problems"]
    mcp_diff = asyncio.run(srv.diff(str(vi), str(vi)))
    assert mcp_diff["status"] == "Stopped" and mcp_diff["problems"]


def test_a_declined_render_in_a_directory_is_never_a_failure_without_a_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    shutil.copy(GOOD_VI, root / "Good.vi")
    monkeypatch.setattr("lvkit.render.body.render_body_with_name", lambda *a, **k: None)
    code = _lvkit(monkeypatch, "render", str(root))
    captured = capsys.readouterr()
    assert "1 failed" in captured.out
    assert "warning: Good.vi: render: nothing to draw" in captured.err
    assert code == 1


@pytest.mark.parametrize(
    "command,target,argv",
    [
        (
            "generate",
            "lvkit.pipeline.generate_python",
            ["generate", "{vi}", "-o", "{o}"],
        ),
        (
            "unresolved",
            "lvkit.unresolved.collect_unresolved",
            ["unresolved", "{vi}"],
        ),
        (
            "docs",
            "lvkit.docs.generate.generate_documents",
            ["docs", "{vi}", "{o}"],
        ),
        ("describe", "lvkit.graph.load_vi_by_path", ["describe", "{vi}"]),
    ],
)
def test_an_lvkit_error_in_any_command_ends_with_the_summary_and_a_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    target: str,
    argv: list[str],
) -> None:
    vi = _project(tmp_path) / "Good.vi"

    def explode(*args: object, **kwargs: object) -> None:
        raise KeyError("boom")

    monkeypatch.setattr(target, explode)
    out = tmp_path / "out"
    code = _lvkit(
        monkeypatch, *(a.format(vi=vi, o=out) for a in argv)
    )
    err = capsys.readouterr().err
    assert code == 1
    assert "KeyError: 'boom'" in err and "Traceback" in err
    assert "Stopped: 1 problem(s) (includes an lvkit error)" in err
    assert "bug in lvkit" in err


def test_the_failed_shallow_load_is_not_lost_when_render_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vi = _project(tmp_path) / "Good.vi"
    real = InMemoryVIGraph.load_vi
    calls: list[LoadMode] = []

    def flaky(self, path, mode=LoadMode.FULL, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(mode)
        if len(calls) == 1:
            raise KeyError("first attempt")
        return real(self, path, mode, *args, **kwargs)

    monkeypatch.setattr(InMemoryVIGraph, "load_vi", flaky)
    svg, _, _, problems = render_vi_file_report(vi, mode=LoadMode.MINIMAL)
    assert svg is not None and calls == [LoadMode.MINIMAL, LoadMode.NONE]
    (problem,) = problems
    assert problem.kind is ProblemKind.LVKIT_ERROR
    assert "first attempt" in problem.reason


def test_an_unreadable_control_dependency_is_a_problem_with_pylabview_s_reason(
    tmp_path: Path,
) -> None:
    ctl = tmp_path / "Bad.ctl"
    ctl.write_bytes(b"not a labview file")
    graph = InMemoryVIGraph()
    key = graph.load_typedef(ctl)
    (problem,) = graph.problems([key])
    assert problem.kind is ProblemKind.UNREADABLE
    assert "pylabview could not read the file" in problem.reason
    assert graph._dep_graph.nodes[key]["node_type"] is NodeType.TYPEDEF


def test_an_unreadable_private_data_control_is_one_problem_on_the_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = InMemoryVIGraph()
    cls_key = str((tmp_path / "C.lvclass").resolve())
    graph._add_stub(cls_key, NodeType.CLASS, "C.lvclass", None, None, None)
    ctl = tmp_path / "C.ctl"
    ctl.write_bytes(b"not a labview file")
    monkeypatch.setattr(graph, "_find_private_data_ctl", lambda *a: ctl)
    cls = SimpleNamespace(name="C", private_data_url="C.ctl")
    graph._load_private_data_ctl(cls_key, cls, tmp_path / "C.lvclass")  # type: ignore[arg-type]
    assert graph.problems([cls_key]) == []
    (problem,) = graph.problems()
    assert problem.vi == "C.ctl" and "pylabview could not read" in problem.reason


def test_a_parse_failure_on_a_control_is_an_lvkit_error_not_an_unreadable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctl = tmp_path / "C.ctl"
    ctl.write_text("x", encoding="utf-8")
    monkeypatch.setattr(InMemoryVIGraph, "_extract", lambda self, p: (p, None, p))

    def parse_bug(*args: object, **kwargs: object) -> None:
        raise ValueError("parser bug")

    monkeypatch.setattr("lvkit.graph.loading.parse_type_map_rich", parse_bug)
    graph = InMemoryVIGraph()
    key = graph.load_typedef(ctl)
    (problem,) = graph.problems([key])
    assert problem.kind is ProblemKind.LVKIT_ERROR and "parser bug" in problem.reason


def test_an_lvlib_member_that_is_not_on_disk_is_a_problem_not_a_log_line(
    tmp_path: Path,
) -> None:
    lvlib = tmp_path / "Lib.lvlib"
    lvlib.write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n<Library LVVersion=\"25008000\">\n"
        '\t<Item Name="gone.vi" Type="VI" URL="gone.vi"/>\n</Library>\n',
        encoding="utf-8",
    )
    run = RunProblems(RunSettings(root=tmp_path))
    assert _collect_library_vis(lvlib, run) == []
    (problem,) = run.report().problems
    assert problem.vi == "Lib.lvlib" and "gone.vi" in problem.reason


# ---- M2: page names ----------------------------------------------------------------


def _names(
    tmp_path: Path, files: list[str], **roots: Path
) -> dict[str, tuple[str, str]]:
    project = tmp_path / "proj"
    for rel in files:
        target = project / rel if not rel.startswith("/") else Path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(GOOD_VI, target)
    graph = InMemoryVIGraph()
    if roots:
        graph.set_library_roots(**roots)
    for rel in files:
        graph.load_vi(project / rel, LoadMode.NONE)
    found = page_names(graph, graph.list_vis(), project)
    return {Path(k).name: (v.group, v.stem) for k, v in found.items()}


def test_a_ranked_page_name_is_never_one_that_is_taken(tmp_path: Path) -> None:
    names = _names(tmp_path, ["sub/a b.vi", "sub/a_b.vi", "sub/a_b-2.vi"])
    assert len({stem for _, stem in names.values()}) == 3


def test_non_ascii_names_keep_their_letters_and_stay_distinct(tmp_path: Path) -> None:
    names = _names(tmp_path, ["sub/測定.vi", "sub/設定.vi"])
    assert names["測定.vi"][1] == "sub_測定"
    assert names["設定.vi"][1] == "sub_設定"


def test_names_that_differ_only_in_case_do_not_share_a_page(tmp_path: Path) -> None:
    """No files on disk: a case-insensitive file system cannot hold both, but the
    naming function must still tell them apart (the output folder may be one)."""
    project = tmp_path / "proj"
    keys = [str(project / "sub" / "A.vi"), str(project / "sub" / "a.vi")]
    graph = SimpleNamespace(get_vi_source_path=lambda key: Path(key))
    found = page_names(graph, keys, project)  # type: ignore[arg-type]
    stems = [found[key].stem for key in keys]
    assert len({stem.casefold() for stem in stems}) == 2
    assert stems == ["sub_A", "sub_a-2"] or stems == ["sub_a", "sub_A-2"]


def test_a_vi_outside_the_project_is_grouped_by_its_real_root(tmp_path: Path) -> None:
    userlib = tmp_path / "user.lib"
    sibling = tmp_path / "shared" / "tools"
    names = _names(
        tmp_path,
        [str(userlib / "U.vi"), str(sibling / "T.vi")],
        userlib_root=userlib,
    )
    assert names["U.vi"] == ("user.lib", "U")
    assert names["T.vi"] == ("tools", "T")


# ---- M3: unresolved --fail-fast, per-thread stdout --------------------------------


def test_unresolved_fail_fast_stops_before_generating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("generation started after the first problem")

    monkeypatch.setattr("lvkit.pipeline.create_import_resolver", must_not_run)
    root = _project(tmp_path)
    code = _lvkit(monkeypatch, "unresolved", str(root), "--fail-fast")
    err = capsys.readouterr().err
    assert code == 1 and "Stopped: 1 problem(s)" in err


def test_capturing_stdout_leaves_other_threads_alone(
    capsys: pytest.CaptureFixture[str],
) -> None:
    started, written = threading.Event(), threading.Event()
    captured: list[str] = []

    def extracting() -> None:
        with capture_stdout() as sink:
            started.set()
            written.wait(5)
            print("narration")
        captured.append(sink.getvalue())

    worker = threading.Thread(target=extracting)
    worker.start()
    started.wait(5)
    print("from another thread")
    written.set()
    worker.join()
    assert captured == ["narration\n"]
    assert "from another thread" in capsys.readouterr().out


# ---- M4: one label per VI, from the project root ----------------------------------


def test_a_directory_render_labels_a_vi_from_the_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    caller = _with_unreadable_subvi(tmp_path)
    _lvkit(monkeypatch, "render", str(caller.parent.parent))  # the Lib2 folder
    assert "warning: Lib2/Class/Do.vi: render:" in capsys.readouterr().err


# ---- LOW ---------------------------------------------------------------------------


def test_a_run_with_an_lvkit_error_says_so_in_its_summary() -> None:
    problem = Problem(
        "A.vi", ProblemStage.LOAD, ProblemKind.LVKIT_ERROR, "KeyError: 'x'"
    )
    report = RunReport.of([problem])
    head = "Partial: 1 problem(s) (includes an lvkit error)"
    assert report.summary_lines()[0] == head
    assert report.exit_code() == EXIT_STOPPED


def test_problems_keep_the_node_type_of_what_they_are_about(tmp_path: Path) -> None:
    graph = InMemoryVIGraph()
    class_key = str((tmp_path / "C.lvclass").resolve())
    problem = Problem(
        "C.lvclass", ProblemStage.GENERATE, ProblemKind.LVKIT_ERROR, "boom"
    )
    graph.record_problem(class_key, problem, NodeType.CLASS)
    assert graph._dep_graph.nodes[class_key]["node_type"] is NodeType.CLASS
    llb = tmp_path / "Lib.llb"
    llb.write_text("not an llb", encoding="utf-8")
    graph.load_llb(llb, LoadMode.NONE)
    assert graph._dep_graph.nodes[str(llb.resolve())]["node_type"] is NodeType.LIBRARY


def test_a_cache_hit_of_an_llb_does_not_rewrite_its_sentinel() -> None:
    import tempfile

    from lvkit import extractor
    from tests.test_llb_stream_lifetime import _MEMBERS, _write_archive

    with tempfile.TemporaryDirectory() as tmp:
        archive = _write_archive(Path(tmp), "UCRF", _MEMBERS)
        first = extractor.extract_llb_members(archive).cache_dir
        sentinel = first / ".extracted"
        before = sentinel.stat().st_mtime_ns
        extractor.extract_llb_members(archive)
        assert sentinel.stat().st_mtime_ns == before
        assert extractor.llb_container_of(first / "first.vi") == archive.resolve()
        assert extractor.llb_container_of(archive) is None  # no sentinel beside it


def test_a_bad_sql_statement_still_prints_the_index_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    args = argparse.Namespace(
        input_path=str(root),
        sql="DELETE FROM vi",
        schema=False,
        format="table",
        no_refresh=False,
    )
    assert cmd_query(args) == 2
    err = capsys.readouterr().err
    assert "query error" in err and "Partial: 1 problem(s)" in err


# ---- warming failures are problems that never stop the command --------------------


def test_a_warming_failure_is_a_problem_on_the_vi_and_does_not_stop_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lvkit.index import build

    root = _project(tmp_path)
    (root / "Bad.vi").unlink()
    graph = RunProblems(RunSettings(fail_fast=True)).new_graph()  # fail-fast on
    graph.load_vi(root / "Good.vi", LoadMode.MINIMAL)

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(build, "store_save", disk_full)
    build.warm_all_loaded(graph)  # must not raise, even in a fail-fast run
    (problem,) = graph.problems([str((root / "Good.vi").resolve())])
    assert problem.kind is ProblemKind.ENVIRONMENT
    assert problem.stage is ProblemStage.CACHE
    assert "could not update the index at" in problem.reason
    assert "No space left on device" in problem.reason


def test_a_vi_that_cannot_be_projected_is_a_problem_and_the_rest_is_warmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lvkit.index import build

    root = _project(tmp_path)
    (root / "Bad.vi").unlink()
    graph = InMemoryVIGraph()
    graph.load_vi(root / "Good.vi", LoadMode.MINIMAL)

    def broken(*args: object, **kwargs: object) -> None:
        raise KeyError("projection")

    monkeypatch.setattr(build, "project_vi_facts", broken)
    build.warm_all_loaded(graph)
    (problem,) = graph.problems()
    assert problem.kind is ProblemKind.LVKIT_ERROR and "projection" in problem.reason


def test_describe_reports_a_warming_failure_in_its_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from lvkit.index import build

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(build, "store_save", disk_full)
    code = _lvkit(monkeypatch, "describe", str(_project(tmp_path) / "Good.vi"))
    err = capsys.readouterr().err
    assert code == 3  # the output was printed; the VI is just not indexed
    assert "could not update the index" in err and "Partial: 1 problem(s)" in err


# ---- the re-review's findings ------------------------------------------------------


def test_generate_that_stops_still_lists_every_problem_recorded_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "p3"
    root.mkdir()
    (root / "Bad.vi").write_text("not a vi", encoding="utf-8")
    shutil.copy(GOOD_VI, root / "x y.vi")
    shutil.copy(GOOD_VI, root / "x_y.vi")
    code = _lvkit(monkeypatch, "generate", str(root), "-o", str(tmp_path / "out"))
    err = capsys.readouterr().err
    assert code == 1
    assert "Stopped: 2 problem(s)" in err
    summary = err[err.index("Stopped:") :]
    assert "warning: Bad.vi: extract:" in summary
    assert "warning: p3: generate:" in summary  # the folder, never "."
    assert str(tmp_path) not in err  # the collision names both VIs relative


def test_a_stopped_run_lists_the_problems_of_graphs_the_command_built() -> None:
    run = RunProblems(RunSettings())
    graph = run.new_graph()
    graph.record_problem(
        "/p/A.vi",
        Problem("A.vi", ProblemStage.LOAD, ProblemKind.UNREADABLE, "boom"),
    )
    assert [p.vi for p in run.report().problems] == ["A.vi"]  # no collect needed
    short_lived = run.new_graph(tracked=False)
    short_lived.record_problem(
        "/p/B.vi", Problem("B.vi", ProblemStage.LOAD, ProblemKind.UNREADABLE, "x")
    )
    assert [p.vi for p in run.report().problems] == ["A.vi"]
    run.collect(short_lived)
    assert [p.vi for p in run.report().problems] == ["A.vi", "B.vi"]


def test_an_index_that_cannot_be_written_stops_the_index_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from lvkit.index import build

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(build, "store_save", disk_full)
    root = _project(tmp_path)
    (root / "Bad.vi").unlink()
    args = argparse.Namespace(input_path=str(root), refresh=False)
    assert cmd_index(args) == EXIT_STOPPED
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "Stopped"
    (problem,) = [p for p in payload["problems"] if p["stage"] == "cache"]
    assert problem["kind"] == "environment" and problem["hint"] is None


def test_one_failed_index_write_is_one_problem_not_one_per_vi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lvkit.index import build

    root = _project(tmp_path)
    (root / "Bad.vi").unlink()
    shutil.copy(GOOD_VI, root / "Second.vi")
    graph = InMemoryVIGraph()
    graph.load_directory(root, LoadMode.MINIMAL)

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(build, "store_save", disk_full)
    build.warm_all_loaded(graph)
    assert len(graph.problems()) == 1


def test_an_index_warming_failure_is_not_stored_with_a_cached_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from lvkit.index import build

    vi = _project(tmp_path) / "Good.vi"
    real = build.store_save

    def disk_full(*args: object, **kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(build, "store_save", disk_full)
    assert _lvkit(monkeypatch, "render", str(vi), "--format", "svg") == 3
    assert "could not update the index" in capsys.readouterr().err
    monkeypatch.setattr(build, "store_save", real)  # the disk is fixed
    assert _lvkit(monkeypatch, "render", str(vi), "--format", "svg") == 0  # a hit
    assert "could not update the index" not in capsys.readouterr().err


def test_an_extraction_cache_failure_is_that_vi_s_problem_in_a_multi_vi_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from lvkit import extractor

    real = extractor._publish
    calls: list[int] = []

    def flaky(*args: object, **kwargs: object) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(extractor, "_publish", flaky)
    root = _project(tmp_path)
    (root / "Bad.vi").unlink()
    shutil.copy(GOOD_VI, root / "Second.vi")
    args = argparse.Namespace(input_path=str(root), refresh=False)
    assert cmd_index(args) == EXIT_PARTIAL  # one VI indexed, one reported
    payload = json.loads(capsys.readouterr().out)
    assert payload["vis"] == 1 and payload["status"] == "Partial"
    # ... but a command whose only VI cannot be cached has no output: it stops.
    calls.clear()
    third = root / "Third.vi"
    shutil.copy(GOOD_VI, third)
    assert _lvkit(monkeypatch, "describe", str(third)) == 1
    assert "could not write the extraction cache" in capsys.readouterr().err


def test_rendering_an_unreadable_vi_prints_its_warning_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path)
    assert _lvkit(monkeypatch, "render", str(root / "Bad.vi")) == 1
    lines = capsys.readouterr().err.splitlines()
    assert len([ln for ln in lines if ln.startswith("warning: Bad.vi: ")]) == 1


def test_render_directory_counts_one_problem_for_one_bad_vi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    caller = _with_unreadable_subvi(tmp_path)
    _lvkit(monkeypatch, "render", str(caller.parent))  # Test.vi and the bad Do.vi
    err = capsys.readouterr().err
    assert "problem(s)" in err and ": 1 problem(s)" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["describe", "{missing}"],
        ["render", "{missing}"],
        ["generate", "{missing}", "-o", "{out}"],
        ["docs", "{missing}", "{out}"],
        ["unresolved", "{missing}"],
        ["visualize", "{missing}"],
        ["structure", "{missing}"],
    ],
)
def test_a_missing_input_is_a_usage_error_with_no_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    missing = tmp_path / "nope.vi"
    code = _lvkit(
        monkeypatch, *(a.format(missing=missing, out=tmp_path / "out") for a in argv)
    )
    err = capsys.readouterr().err
    assert code == 2 and "Path not found" in err and "Traceback" not in err


def test_a_non_vi_input_to_describe_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lvlib = tmp_path / "L.lvlib"
    lvlib.write_text("<Library/>", encoding="utf-8")
    assert _lvkit(monkeypatch, "describe", str(lvlib)) == 2
    assert "Traceback" not in capsys.readouterr().err


def test_a_fail_fast_stop_summarizes_everything_recorded_so_far(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from lvkit.cli_run import command_run, run_command
    from lvkit.run_report import RunStopped

    def command(args: argparse.Namespace) -> int:
        run = command_run(args, root=Path("/p"))
        run.fail(
            Path("/p/A.vi"),
            ProblemStage.LOAD,
            UnreadableVIError("first"),
        )
        raise RunStopped(
            Problem("B.vi", ProblemStage.LOAD, ProblemKind.UNREADABLE, "second")
        )

    assert run_command(command, argparse.Namespace()) == EXIT_STOPPED
    err = capsys.readouterr().err
    assert "Stopped: 2 problem(s)" in err
    assert "A.vi" in err and "B.vi" in err


def test_fail_labels_a_library_vi_and_an_llb_member_like_the_graph_does(
    tmp_path: Path,
) -> None:
    from lvkit import extractor
    from tests.test_llb_stream_lifetime import _MEMBERS, _write_archive

    vilib = tmp_path / "vi.lib"
    (vilib / "Utility").mkdir(parents=True)
    run = RunProblems(RunSettings(root=tmp_path / "proj"))
    run.settings.library_roots = {"<vilib>": (vilib,)}
    run.fail(vilib / "Utility" / "Y.vi", ProblemStage.DESCRIBE, UnreadableVIError("x"))
    archive = _write_archive(tmp_path / "arch", "UCRF", _MEMBERS)
    cache = extractor.extract_llb(archive)
    run.fail(cache / "first.vi", ProblemStage.DESCRIBE, UnreadableVIError("y"))
    labels = sorted(p.vi for p in run.report().problems)
    assert labels == ["<vilib>/Utility/Y.vi", "members.llb/first.vi"]


def test_two_roots_each_named_for_what_it_answers_and_no_copies(
    tmp_path: Path,
) -> None:
    """The reviewer's probe: ``repo/.git`` + ``repo/LabVIEW/My.lvproj``. Labels and
    generated modules start at the ``.lvproj`` folder; the index is the repo's."""
    from lvkit import cache_paths, module_layout, run_report

    assert not hasattr(module_layout, "project_root")
    assert not hasattr(run_report, "project_root_of")
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    labview = tmp_path / "repo" / "LabVIEW"
    (labview / "sub").mkdir(parents=True)
    (labview / "My.lvproj").write_text("<Project/>", encoding="utf-8")
    vi = labview / "sub" / "A.vi"
    shutil.copy(GOOD_VI, vi)
    assert cache_paths.project_root(vi) == labview.resolve()
    assert cache_paths.store_root(vi) == (tmp_path / "repo").resolve()
    assert RunSettings().label(vi) == "sub/A.vi"
    from lvkit.index.project import resolve_project_files

    index_root, _, _ = resolve_project_files(vi)
    assert index_root == (tmp_path / "repo").resolve()


def test_an_unreadable_file_merges_across_stages_but_an_lvkit_error_does_not() -> None:
    def problem(stage: ProblemStage, kind: ProblemKind) -> Problem:
        return Problem("A.vi", stage, kind, "same reason")

    unreadable = RunReport.of(
        [
            problem(ProblemStage.EXTRACT, ProblemKind.UNREADABLE),
            problem(ProblemStage.RENDER, ProblemKind.UNREADABLE),
        ]
    )
    assert len(unreadable.problems) == 1
    errors = RunReport.of(
        [
            problem(ProblemStage.INDEX, ProblemKind.LVKIT_ERROR),
            problem(ProblemStage.RENDER, ProblemKind.LVKIT_ERROR),
        ]
    )
    assert [p.stage for p in errors.problems] == [
        ProblemStage.INDEX,
        ProblemStage.RENDER,
    ]


def test_no_module_level_run_registry_and_the_run_travels_with_the_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from lvkit import cli_run
    from lvkit.run_report import RunStopped

    assert not hasattr(cli_run, "_active")

    def command(args: argparse.Namespace) -> int:
        cli_run.command_run(args, root=Path("/p")).fail(
            Path("/p/A.vi"), ProblemStage.LOAD, UnreadableVIError("first")
        )
        raise RunStopped(
            Problem("B.vi", ProblemStage.LOAD, ProblemKind.UNREADABLE, "second")
        )

    args = argparse.Namespace()
    assert cli_run.run_command(command, args) == EXIT_STOPPED
    assert "Stopped: 2 problem(s)" in capsys.readouterr().err


def test_diff_open_with_a_non_html_format_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    vi = _project(tmp_path) / "Good.vi"
    code = _lvkit(monkeypatch, "diff", str(vi), str(vi), "--open", "--format", "json")
    assert code == 2 and "--open requires --format html" in capsys.readouterr().err
