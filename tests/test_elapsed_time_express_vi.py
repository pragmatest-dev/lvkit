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
from lvkit.pipeline import generate_python
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
    instance_defaults: dict[int, str | None] | None = None,
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
        instance_defaults=instance_defaults,
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


def _unwired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: list[float],
    instance_defaults: dict[int, str | None] | None,
    gaps: list[Gap] | None = None,
) -> list:
    """Calls with every input unwired, so each takes the setting the caller's
    instance of the Express VI saves."""
    return _run(
        tmp_path,
        monkeypatch,
        [{} for _ in clock],
        clock,
        inputs={},
        outputs={"has_elapsed": (17, BOOL), "start": (11, DBL)},
        pane=[(0, "input", DBL), (2, "input", BOOL), (3, "input", BOOL)]
        + [(8, "input", DBL), (11, "output", DBL), (17, "output", BOOL)],
        instance_defaults=instance_defaults,
        gaps=gaps,
    )


def test_unwired_inputs_take_the_settings_the_caller_saves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The caller's instance saves Time Target 5 and Auto Reset TRUE (the values
    # of the Express VI's dialog in Kill Service.vi): the call that reaches 5
    # seconds reports it and the next one measures from that call.
    first, second, third, fourth = _unwired(
        tmp_path,
        monkeypatch,
        [10.0, 14.0, 15.0, 16.0],
        {0: "5.0", 2: None, 3: "True", 8: None},
    )
    assert [r.has_elapsed for r in (first, second, third, fourth)] == [
        False,
        False,
        True,
        False,
    ]
    assert fourth.start == 15.0


def test_a_control_the_instance_saves_no_default_for_holds_its_type_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Nothing saved: Time Target 0.0 (not NI's dialog default 1), Auto Reset FALSE.
    first, second = _unwired(
        tmp_path,
        monkeypatch,
        [10.0, 10.5],
        {0: None, 2: None, 3: None, 8: None},
    )
    assert (first.has_elapsed, second.has_elapsed) == (True, True)
    assert second.start == 10.0


def test_an_unwired_auto_reset_of_a_call_with_no_instance_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError, match="Auto Reset setting"):
        _clock(monkeypatch, [1.0])
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*EXPRESS_LLB, "subElapsedTime.vi"],
            pane=[(3, "input", BOOL), (17, "output", BOOL)],
            inputs={},
            outputs={"has_elapsed": (17, BOOL)},
            arguments={},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert gaps


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


@pytest.mark.needs_samples
def test_kill_service_reads_its_elapsed_time_settings_from_the_file(
    tmp_path: Path,
) -> None:
    """Kill Service.vi's Elapsed Time Express VI leaves Auto Reset unwired; the
    caller's instance of the Express VI saves it TRUE (and Time Target 5)."""
    vi = Path(
        ".lvkit/cache/samples/measurement-plugin-labview/Source/Tests/Tests.Runtime/"
        "Measurement Server/Helpers/Kill Service.vi"
    )
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    result = generate_python(vi, tmp_path)
    source = Path(result["vi_paths"][str(vi.resolve())]).read_text(encoding="utf-8")
    assert "time_has_elapsed = elapsed_time >= 5.0" in source
    assert (
        "if time_has_elapsed:\n"
        "                _lv_elapsed_start_kill_service_1100 = present" in source
    )
    assert not result["gaps"]


@pytest.mark.needs_samples
def test_kill_service_call_records_the_instances_saved_settings() -> None:
    from lvkit.graph import load_vi_by_path
    from lvkit.graph.models import VINode
    from lvkit.load_mode import LoadMode

    vi = Path(
        ".lvkit/cache/samples/measurement-plugin-labview/Source/Tests/Tests.Runtime/"
        "Measurement Server/Helpers/Kill Service.vi"
    )
    if not vi.exists():
        pytest.skip(f"sample corpus VI not present: {vi}")
    graph, _ = load_vi_by_path(vi, LoadMode.MINIMAL, layout=False)
    calls = [
        n
        for _, data in graph._graph.nodes(data=True)  # noqa: SLF001
        if isinstance(n := data.get("node"), VINode) and n.instance_defaults
    ]
    (call,) = calls
    # Time Target, Reset, Auto Reset, error in, Set Start Time: the controls of the
    # instance by connector-pane slot; only Time Target and Auto Reset save a value.
    assert call.instance_defaults == {0: "5.0", 2: None, 3: "True", 5: None, 8: None}
