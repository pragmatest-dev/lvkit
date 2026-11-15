"""Elapsed Time Express VI (``subElapsedTime.vi``): each caller diagram is
generated from the catalog entry and its Python is run, a call after another
(the VI keeps its start time between calls).

The pane is the one the caller Kill Service.vi (measurement-plugin-labview) saves
for the call; the behaviour is what NI's public page for the Express VI says."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from tests.vilib_caller import BOOL, DBL, ERROR, STRING, run_catalog_call

EXPRESS_LLB = [
    "<vilib>",
    "express",
    "express execution control",
    "ElapsedTimeBlock.llb",
]

# Every terminal of the call node, as the caller saves them (indices without a
# name in the entry are the hidden ones of the Express VI).
PANE = [
    (0, "input", DBL),
    (2, "input", BOOL),
    (3, "input", BOOL),
    (5, "input", ERROR),
    (8, "input", DBL),
    (11, "output", DBL),
    (13, "output", STRING),
    (14, "output", DBL),
    (15, "output", STRING),
    (16, "output", DBL),
    (17, "output", BOOL),
    (18, "output", STRING),
    (19, "output", ERROR),
]
INPUTS = {
    "time_target": (0, DBL),
    "reset": (2, BOOL),
    "auto_reset": (3, BOOL),
    "set_start_time": (8, DBL),
}
OUTPUTS = {
    "start": (11, DBL),
    "present": (14, DBL),
    "elapsed": (16, DBL),
    "has_elapsed": (17, BOOL),
}


def _clock(monkeypatch: pytest.MonkeyPatch, seconds: list[float]) -> None:
    """Make ``time.time`` read the given seconds since 1904, one per call (the
    Unix time is those seconds less the 66 years to 1970)."""
    ticks = iter(seconds)
    monkeypatch.setattr(time, "time", lambda: next(ticks) - 2082844800)


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict],
    clock: list[float],
    inputs: dict = INPUTS,
    outputs: dict = OUTPUTS,
    pane: list = PANE,
    gaps: list[Gap] | None = None,
) -> list:
    _clock(monkeypatch, clock)
    source, results = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*EXPRESS_LLB, "subElapsedTime.vi"],
        pane=pane,
        inputs=inputs,
        outputs=outputs,
        arguments={},
        calls=calls,
        gaps=gaps,
    )
    assert "lvkit" not in source
    return results


def _call(target=1.0, reset=False, auto=False, set_start=0.0) -> dict:
    return {
        "time_target": target,
        "reset": reset,
        "auto_reset": auto,
        "set_start_time": set_start,
    }


def test_the_first_call_starts_at_the_present_and_has_not_elapsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (first,) = _run(tmp_path, monkeypatch, [_call()], [1000.0])
    assert first.present == 1000.0
    assert first.start == 1000.0
    assert first.elapsed == 0.0
    assert first.has_elapsed is False


def test_a_later_call_measures_from_the_first_and_reports_the_target_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, third = _run(
        tmp_path,
        monkeypatch,
        [_call(target=2.0)] * 3,
        [1000.0, 1001.5, 1002.0],
    )
    assert (second.elapsed, second.has_elapsed) == (1.5, False)
    assert (third.elapsed, third.has_elapsed, third.start) == (2.0, True, 1000.0)


def test_without_auto_reset_the_start_time_stays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    *_, last = _run(tmp_path, monkeypatch, [_call(target=1.0)] * 3, [10.0, 12.0, 13.0])
    assert (last.elapsed, last.has_elapsed, last.start) == (3.0, True, 10.0)


def test_auto_reset_starts_over_from_the_call_that_reached_the_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, third = _run(
        tmp_path,
        monkeypatch,
        [_call(target=1.0, auto=True)] * 3,
        [10.0, 11.0, 11.5],
    )
    assert (second.elapsed, second.has_elapsed) == (1.0, True)
    assert (third.start, third.elapsed, third.has_elapsed) == (11.0, 0.5, False)


def test_reset_starts_again_from_the_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second, third = _run(
        tmp_path,
        monkeypatch,
        [_call(), _call(reset=True), _call()],
        [10.0, 50.0, 50.25],
    )
    assert (second.start, second.elapsed) == (50.0, 0.0)
    assert (third.start, third.elapsed) == (50.0, 0.25)


def test_set_start_time_is_the_start_time_and_zero_is_the_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (first,) = _run(tmp_path, monkeypatch, [_call(set_start=990.0)], [1000.0])
    assert (first.start, first.elapsed, first.has_elapsed) == (990.0, 10.0, True)


def test_unwired_inputs_take_the_documented_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Time Target (s) 1, Reset FALSE; nothing wired but the Time has Elapsed output.
    first, second = _run(
        tmp_path,
        monkeypatch,
        [{}, {}],
        [5.0, 6.0],
        inputs={},
        outputs={"has_elapsed": (17, BOOL)},
        pane=[(17, "output", BOOL)],
    )
    assert (first.has_elapsed, second.has_elapsed) == (False, True)


def test_a_wired_text_output_is_a_gap_that_raises_where_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    _clock(monkeypatch, [1.0])
    with pytest.raises(NotImplementedError, match="format of its text"):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*EXPRESS_LLB, "subElapsedTime.vi"],
            pane=PANE,
            inputs={},
            outputs={"text": (15, STRING)},
            arguments={},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert gaps
