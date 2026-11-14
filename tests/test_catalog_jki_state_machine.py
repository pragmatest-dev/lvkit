"""JKI State Machine's Add State(s) to Queue and Parse State Queue: each caller
diagram is generated from the catalog entry the recorded path names (the connector
pane numbers are the ones the corpus callers record), and its Python is run.

The cases are those of JKI's own unit tests (``tests/testStateMachineUtilities``
of the JKI State Machine source: ``testAddStatesToQueue*`` and
``testParse*``), each with the constants and the expected string the test VI
holds."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest

from tests.test_catalog_corpus_callers import SAMPLES, _generate
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.vilib_caller import STRING, run_catalog_call

LLB = [
    "<vilib>",
    "addons",
    "_JKI Toolkits",
    "State Machine",
    "_jki_lib_state_machine.llb",
]

ADD_RECORDED = [*LLB, "Add State(s) to Queue__jki_lib_state_machine.vi"]
ADD_PANE = [
    (0, "input", STRING),
    (5, "input", STRING),
    (6, "output", STRING),
    (7, "input", STRING),
]
ADD_INPUTS = {
    "front": (0, STRING),
    "middle": (5, STRING),
    "back": (7, STRING),
}


def _add(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **given: str,
) -> Any:
    wired = {name: ADD_INPUTS[name] for name in given}
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        ADD_RECORDED,
        ADD_PANE,
        wired,
        {"out": (6, STRING)},
        given,
    )
    return result.out


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # testAddStatesToQueue
        ({"front": "A", "middle": "B", "back": "C\nD"}, "A\nB\nC\nD"),
        # testAddStatesToQueueEolHandling1
        ({"front": "\r\nA\r\n", "middle": "\r\nB\r\n"}, "A\nB"),
        # testAddStatesToQueueEolHandling2
        ({"front": "\r\nA\r\n"}, "A"),
        # testAddStatesToQueueEolHandling3
        ({"front": "\r\nA\r\n", "middle": "\r\nB\r\n", "back": "\r\nC\r\n"}, "A\nB\nC"),
        # testAddStatesToQueueWhitespaceHandling1
        ({"front": " A ", "middle": " B ", "back": " C "}, "A\nB\nC"),
        # testAddStatesToQueueWhitespaceHandling2
        ({"front": " A", "middle": "B ", "back": " C "}, "A\nB\nC"),
        # testAddStatesToQueueWhitespaceHandling3
        ({"middle": "B ", "back": " C "}, "B\nC"),
        # a part that is only white space is not a state
        ({"front": "A", "middle": " \t\r\n", "back": "C"}, "A\nC"),
        ({}, ""),
    ],
)
def test_add_states_to_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    given: dict[str, str],
    expected: str,
) -> None:
    assert _add(tmp_path, monkeypatch, **given) == expected


PARSE_RECORDED = [*LLB, "Parse State Queue__jki_lib_state_machine.vi"]
PARSE_PANE = [
    (0, "input", STRING),
    (4, "output", STRING),
    (6, "output", STRING),
    (7, "input", ERROR_CLUSTER),
    (8, "output", STRING),
]
PARSE_OUTPUTS = {
    "remaining": (4, STRING),
    "current": (6, STRING),
    "arguments": (8, STRING),
}


def _parse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    queue: str,
    error: object | None = None,
) -> tuple[str, str, str]:
    inputs: dict[str, Any] = {"queue": (0, STRING)}
    arguments: dict[str, Any] = {"queue": queue}
    if error is not None:
        inputs["error_in"] = (7, ERROR_CLUSTER)
        arguments["error_in"] = error
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        PARSE_RECORDED,
        PARSE_PANE,
        inputs,
        PARSE_OUTPUTS,
        arguments,
    )
    return result.current, result.arguments, result.remaining


@pytest.mark.parametrize(
    ("queue", "expected"),
    [
        # testParseStateQueueEmptyString
        ("", ("", "", "")),
        # testParseArguments
        ("state >> args # this is a command", ("state", "args", "")),
        # testParseStateQueueSimple: the first of 'One\nTwo\nThree'
        ("One\nTwo\nThree", ("One", "", "Two\nThree")),
        ("Two\nThree", ("Two", "", "Three")),
        ("Three", ("Three", "", "")),
        # testParseStateQueueWhitespaceTest1: blank lines between the states
        (
            "Command: State1\n  \t\n\r\nCommand: State 2",
            ("Command: State1", "", "\n\r\nCommand: State 2"),
        ),
        ("\n\r\nCommand: State 2", ("Command: State 2", "", "")),
        # testParseCommentsTrailingHash / SlashSlash / SlashStar: the second pop
        ("Two # my favorite number\nThree", ("Two", "", "Three")),
        ("Two //my favorite number\nThree", ("Two", "", "Three")),
        ("Two /* my favorite number\nThree", ("Two", "", "Three")),
        # testParseMultipleCommentLines
        (
            "#############\n# This is a test\n#############\nThis is a command\n"
            "This the remaining commands",
            ("This is a command", "", "This the remaining commands"),
        ),
        # a state and its arguments, then the rest
        ("One >> Arg\nTwo", ("One", "Arg", "Two")),
        # only blank and comment lines: no state
        ("# one\n\n// two\n", ("", "", "")),
    ],
)
def test_parse_state_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    queue: str,
    expected: tuple[str, str, str],
) -> None:
    assert _parse(tmp_path, monkeypatch, queue) == expected


def test_parse_state_queue_pops_every_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """testParseStateQueueSimple: a For loop over the lines pops each in turn."""
    queue = "One\nTwo\nThree"
    states = []
    for _ in queue.split("\n"):
        current, _arguments, queue = _parse(tmp_path, monkeypatch, queue)
        states.append(current)
    assert states == ["One", "Two", "Three"]
    assert queue == ""


def test_parse_state_queue_error_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """testParseStateQueueErrorTest2: an error in keeps the queue and gives the
    state Error Handler with no arguments."""
    failed = types.SimpleNamespace(status=True, code=42, source="Test")
    assert _parse(tmp_path, monkeypatch, "One >> Arg\nTwo", failed) == (
        "Error Handler",
        "",
        "One >> Arg\nTwo",
    )
    warning = types.SimpleNamespace(status=False, code=42, source="Test")
    assert _parse(tmp_path, monkeypatch, "One >> Arg\nTwo", warning) == (
        "One",
        "Arg",
        "Two",
    )


@pytest.mark.functional
@pytest.mark.needs_samples
def test_corpus_caller_writes_both_vis_inline(tmp_path: Path) -> None:
    """JKI VI Tester's Get Project Members from Path calls both VIs. Its other
    nodes (project references, a front panel) are gaps, so it cannot run: its
    generated source is compiled, and neither VI is a gap in it."""
    vi = (
        SAMPLES
        / "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner"
        / "Graphical Test Runner Support/Get Project Members from Path - SM.vi"
    )
    package = _generate(vi, tmp_path)
    source = next(package.rglob("get_project_members_from_path___sm.py")).read_text(
        encoding="utf-8"
    )
    compile(source, "get_project_members", "exec")
    assert "import re" in source
    assert "'\\n'.join(" in source
    for line in source.splitlines():
        if "NotImplementedError" in line:
            assert "State Queue" not in line
            assert "State(s)" not in line
