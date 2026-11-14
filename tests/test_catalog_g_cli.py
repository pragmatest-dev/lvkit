"""G CLI's Start CLI Interface and Exit With Error Cluster Code: each caller
diagram is generated from the catalog entry the recorded path names (the
connector pane numbers are the ones G CLI's own VIs and their callers record),
and its Python is run.

G CLI is a launcher that starts a LabVIEW program and gives it, over TCP, its
arguments and working directory, and takes back its exit code. A Python program
has its own ``sys.argv``, working directory and ``sys.exit``, so the two VIs are
those. Source of the behaviour: G CLI's own VIs (``Start CLI Interface.vi``,
``Exit With Error Cluster Code.vi``, ``Exit with Code.vi``) and its book
(``docs/book/src/getting_started.md``: "Any arguments after -- are passed to the
application")."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from lvkit.codegen.errors_module import ERRORS_MODULE_SOURCE
from lvkit.models import LVType, LVTypeKind
from tests.corpus_graphs import OPENG_USERLIB
from tests.helpers import exec_generated
from tests.test_catalog_corpus_callers import SAMPLES
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.vilib_caller import I32, PATH, STRING, run_catalog_call

CLI_CLASS = ["<vilib>", "Wiresmith Technology", "G CLI", "CLI Class"]
START_RECORDED = [*CLI_CLASS, "Start CLI Interface.vi"]
EXIT_RECORDED = [*CLI_CLASS, "Exit With Error Cluster Code.vi"]

CLI = LVType(kind=LVTypeKind.CLUSTER, underlying_type="CLI.lvclass")
STRINGS = LVType(kind=LVTypeKind.ARRAY, element_type=STRING)

START_PANE = [
    (8, "input", ERROR_CLUSTER),
    (3, "output", CLI),
    (2, "output", STRINGS),
    (1, "output", PATH),
    (0, "output", ERROR_CLUSTER),
]
EXIT_PANE = [
    (11, "input", CLI),
    (10, "input", I32),
    (8, "input", ERROR_CLUSTER),
    (0, "output", ERROR_CLUSTER),
]


def _start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    error: object | None = None,
) -> tuple[list[str], Path]:
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.chdir(tmp_path)
    inputs: dict = {}
    arguments: dict = {}
    if error is not None:
        inputs["error_in"] = (8, ERROR_CLUSTER)
        arguments["error_in"] = error
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        START_RECORDED,
        START_PANE,
        inputs,
        {"parameters": (2, STRINGS), "directory": (1, PATH)},
        arguments,
    )
    return result.parameters, result.directory


def test_start_gives_the_program_arguments_and_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parameters, directory = _start(tmp_path, monkeypatch, ["tool.py", "a b", "c"])
    assert parameters == ["a b", "c"]
    assert directory == Path.cwd() == tmp_path


def test_start_with_no_arguments_gives_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parameters, _ = _start(tmp_path, monkeypatch, ["tool.py"])
    assert parameters == []


def test_start_with_an_error_in_gives_what_its_error_case_gives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = types.SimpleNamespace(status=True, code=42, source="Test")
    parameters, directory = _start(tmp_path, monkeypatch, ["tool.py", "a"], failed)
    assert parameters == []
    assert directory == Path("C:/")
    warning = types.SimpleNamespace(status=False, code=42, source="Test")
    parameters, directory = _start(tmp_path, monkeypatch, ["tool.py", "a"], warning)
    assert parameters == ["a"]
    assert directory == tmp_path


def _exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    code: int | None = None,
    error: object | None = None,
) -> object:
    inputs: dict = {}
    arguments: dict = {}
    if code is not None:
        inputs["code"] = (10, I32)
        arguments["code"] = code
    if error is not None:
        inputs["error_in"] = (8, ERROR_CLUSTER)
        arguments["error_in"] = error
    with pytest.raises(SystemExit) as stopped:
        run_catalog_call(
            tmp_path, monkeypatch, EXIT_RECORDED, EXIT_PANE, inputs, {}, arguments
        )
    return stopped.value.code


def test_exit_with_no_error_uses_no_error_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _exit(tmp_path, monkeypatch) == 0
    assert _exit(tmp_path, monkeypatch, code=3) == 3


def test_exit_with_an_error_writes_it_to_stderr_and_exits_with_its_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    errors = exec_generated(ERRORS_MODULE_SOURCE, None, "<errors>")
    failed = errors["LabVIEWError"](code=7, source="Test", status=True)
    assert _exit(tmp_path, monkeypatch, code=3, error=failed) == 7
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "==============\nError At Exit:\nError 7 at Test\n==============\n"
    )


def test_exit_with_a_warning_uses_no_error_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    warning = types.SimpleNamespace(status=False, code=7, source="Test")
    assert _exit(tmp_path, monkeypatch, code=3, error=warning) == 3
    assert capsys.readouterr().err == ""


def _generate_run_all_tests_cli(tmp_path: Path, *extra: str) -> tuple[Path, str]:
    """The package generated for the Actor Framework's Run all tests CLI (with the
    search paths ``extra``) and the source of its module."""
    vi = SAMPLES / "actor-framework/Tooling/Run all tests CLI.vi"
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "lvkit",
            "generate",
            str(vi),
            "-o",
            str(tmp_path),
            "--no-auto-vilib",
            "--userlib",
            str(OPENG_USERLIB),
            "--search-path",
            "<topvi>",
            "--search-path",
            "<foundvi>",
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert done.returncode in (0, 3), f"{done.stdout}\n{done.stderr}"
    package = next(p for p in tmp_path.iterdir() if p.is_dir())
    source = next(package.rglob("run_all_tests_cli.py")).read_text(encoding="utf-8")
    compile(source, "run_all_tests_cli", "exec")
    return package, source


def _assert_runs_with_the_python_arguments(source: str) -> None:
    assert "sys.argv[1:]" in source
    assert "sys.exit(" in source
    for line in source.splitlines():
        if "NotImplementedError" in line:
            assert "Start CLI Interface" not in line
            assert "Exit With Error Cluster Code" not in line


@pytest.mark.functional
@pytest.mark.needs_samples
def test_corpus_caller_runs_with_the_python_arguments(tmp_path: Path) -> None:
    """The Actor Framework's Run all tests CLI calls both VIs, G CLI's own VIs not
    being on the VI search path: neither is a gap in its generated source, which
    asks the program for its own arguments."""
    _, source = _generate_run_all_tests_cli(tmp_path)
    _assert_runs_with_the_python_arguments(source)


@pytest.mark.functional
@pytest.mark.needs_samples
def test_corpus_caller_with_g_cli_loaded_still_uses_the_catalog(
    tmp_path: Path,
) -> None:
    """With G CLI's source on the search path the two VIs are members of the loaded
    CLI class, and the catalog still gives their Python: the calls use it and the
    class module has no method for either."""
    if not (SAMPLES / "G-CLI").exists():
        pytest.skip(f"corpus sample missing: {SAMPLES / 'G-CLI'}")
    package, source = _generate_run_all_tests_cli(
        tmp_path, "--search-path", str(SAMPLES / "G-CLI")
    )
    _assert_runs_with_the_python_arguments(source)
    cli = next(package.rglob("cli.py")).read_text(encoding="utf-8")
    assert "def start_cli_interface" not in cli
    assert "def exit_with_error_cluster_code" not in cli
    assert "StartCliInterfaceResult" not in cli
