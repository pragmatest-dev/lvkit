"""An unwired input whose default NI documents as a behaviour: Format Date/Time
String's time stamp (the current date and time), DAQmx Create Virtual Channel's
task (a new task), DAQmx Create Task's name (unnamed). The generated code runs with
the documented default. File dialogs and a count of one element are documented
behaviours generated code cannot write: each stays a gap that names the behaviour."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import hightime

from lvkit.codegen.gaps import Gap
from lvkit.models import LVType, LVTypeKind
from lvkit.primitive_resolver import get_resolver
from lvkit.terminal_default import DefaultBehavior
from lvkit.vilib_resolver import get_resolver as get_vilib_resolver
from tests.native_vi import generate, node, scalar
from tests.test_driver_catalog_calls import _generate

STRING_T = scalar("String")
I32_T = scalar("NumInt32")
DBL_T = scalar("NumFloat64")
REFNUM_T = scalar("Refnum")
PATH_T = scalar("Path")
ERROR_T = LVType(kind=LVTypeKind.CLUSTER)
STAMP_T = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="MeasureData",
    measure_flavor="Timestamp",
)

CREATE = "<vilib>/DAQmx/create/channels.llb"
TASKS = "<vilib>/DAQmx/configure/task.llb"


def _format_stamp(wire_stamp: bool, wire_format: bool) -> object:
    terminals = [(0, "output", STRING_T), (2, "input", STAMP_T), (3, "input", STRING_T)]
    inputs: dict[str, LVType] = {}
    wires = [("fm:0", "fp:out")]
    if wire_stamp:
        inputs["stamp"] = STAMP_T
        wires.append(("fp:stamp", "fm:2"))
    if wire_format:
        inputs["format"] = STRING_T
        wires.append(("fp:format", "fm:3"))
    gaps: list[Gap] = []
    generated = generate(
        inputs, {"out": STRING_T}, [node("fm", 8100, terminals)], wires, gaps=gaps
    )
    assert gaps == []
    return generated.function


def test_an_unwired_time_stamp_is_the_current_date_and_time() -> None:
    function = _format_stamp(wire_stamp=False, wire_format=True)
    before = datetime.datetime.now(datetime.UTC)
    text = function("%Y-%m-%d %H:%M:%S").out  # type: ignore[operator]
    after = datetime.datetime.now(datetime.UTC)
    stamp = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=datetime.UTC
    )
    assert before.replace(microsecond=0) <= stamp <= after


def test_an_unwired_time_format_is_percent_c() -> None:
    function = _format_stamp(wire_stamp=True, wire_format=False)
    when = hightime.datetime(2026, 11, 15, 8, 30, 5, tzinfo=datetime.UTC)
    assert function(when).out == when.strftime("%c")  # type: ignore[operator]


def test_format_date_time_catalog_records_the_documented_defaults() -> None:
    entry = get_resolver().resolve(prim_id="8100")
    assert entry is not None
    by_name = {t.name: t for t in entry.terminals}
    assert by_name["seconds (now)"].default_behavior == DefaultBehavior.CURRENT_TIME
    assert by_name["time format string (%c)"].default == "%c"


def _create_channel(tmp_path: Path, task_wired: bool) -> str:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="DAQmx Create Virtual Channel.vi",
        path=f"{CREATE}/DAQmx Create Channel (AI-Voltage-Basic).vi",
        selector="AI Voltage",
        terminals=[
            (0, "input", "task/channels in", REFNUM_T, "task" if task_wired else ""),
            (1, "input", "maximum value", DBL_T, "high"),
            (2, "input", "minimum value", DBL_T, "low"),
            (4, "output", "task out", REFNUM_T, ""),
            (5, "input", "physical channels", STRING_T, "channels"),
        ],
        gaps=gaps,
    )
    assert gaps == []
    return source


class _FakeChannels:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float, float]] = []

    def add_ai_voltage_chan(self, channel: str, min_val: float, max_val: float) -> None:
        self.calls.append((channel, min_val, max_val))


class _FakeTask:
    made: list[_FakeTask] = []

    def __init__(self, name: str = "") -> None:
        self.name = name
        self.ai_channels = _FakeChannels()
        _FakeTask.made.append(self)


class _FakeNidaqmx:
    Task = _FakeTask


def _run(source: str, namespace: dict[str, object]) -> dict[str, object]:
    markers = ("nidaqmx.Task(", ".add_ai_voltage_chan(")
    lines = [
        line.strip()
        for line in source.splitlines()
        if any(marker in line for marker in markers)
    ]
    for line in lines:
        exec(line, namespace)  # noqa: S102
    return namespace


def test_an_unwired_task_is_a_new_task_the_channel_is_added_to(
    tmp_path: Path,
) -> None:
    source = _create_channel(tmp_path, task_wired=False)
    _FakeTask.made = []
    _run(
        source,
        {
            "nidaqmx": _FakeNidaqmx,
            "channels": "Dev1/ai0",
            "high": 5.0,
            "low": -5.0,
        },
    )
    assert len(_FakeTask.made) == 1
    assert _FakeTask.made[0].ai_channels.calls == [("Dev1/ai0", -5.0, 5.0)]


def test_a_wired_task_is_not_replaced_by_a_new_one(tmp_path: Path) -> None:
    source = _create_channel(tmp_path, task_wired=True)
    assert "nidaqmx.Task(" not in source
    wired = _FakeTask()
    _FakeTask.made = []
    _run(
        source,
        {"task": wired, "channels": "Dev1/ai0", "high": 5.0, "low": -5.0},
    )
    assert _FakeTask.made == []
    assert wired.ai_channels.calls == [("Dev1/ai0", -5.0, 5.0)]


def test_every_create_virtual_channel_records_the_new_task_behavior() -> None:
    catalog = Path(__file__).parents[1] / "src/lvkit/data/drivers/daqmx.json"
    names = [
        e["name"]
        for e in json.loads(catalog.read_text())["entries"]
        if e["name"].startswith("DAQmx Create Virtual Channel (")
    ]
    assert len(names) == 30
    for name in names:
        entry = get_vilib_resolver().resolve_by_name(name)
        assert entry is not None, name
        task_in = next(t for t in entry.terminals if t.name == "task/channels in")
        assert task_in.default_behavior == DefaultBehavior.NEW_TASK, name


def test_an_unwired_task_name_is_an_unnamed_task(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="DAQmx Create Task.vi",
        path=f"{TASKS}/DAQmx Create Task.vi",
        selector=None,
        terminals=[
            (1, "input", "new task name", STRING_T, ""),
            (4, "output", "task out", REFNUM_T, ""),
        ],
        gaps=gaps,
    )
    assert gaps == []
    _FakeTask.made = []
    line = next(ln.strip() for ln in source.splitlines() if "nidaqmx.Task(" in ln)
    exec(line, {"nidaqmx": _FakeNidaqmx})  # noqa: S102
    assert [task.name for task in _FakeTask.made] == [""]


def _read_text_file(file_wired: bool) -> list[Gap]:
    terminals = [
        (0, "output", ERROR_T),
        (2, "output", STRING_T),
        (8, "input", ERROR_T),
        (11, "input", PATH_T),
    ]
    inputs: dict[str, LVType] = {}
    wires = [("rt:2", "fp:text")]
    if file_wired:
        inputs["file"] = PATH_T
        wires.append(("fp:file", "rt:11"))
    gaps: list[Gap] = []
    generate(
        inputs, {"text": STRING_T}, [node("rt", 8070, terminals)], wires, gaps=gaps
    )
    return gaps


def test_an_unwired_file_is_a_gap_that_names_the_dialog() -> None:
    gaps = _read_text_file(file_wired=False)
    assert [g.kind for g in gaps] == ["unknown_default"]
    assert (
        "The input 'file (use dialog)' of Read from Text File is not wired and its "
        "default (file dialog) is not written for this function"
    ) in gaps[0].reason


def test_a_wired_file_has_no_gap() -> None:
    assert _read_text_file(file_wired=True) == []


def test_an_unwired_count_of_binary_elements_is_a_gap_that_names_one_element() -> None:
    terminals = [
        (0, "output", ERROR_T),
        (2, "output", STRING_T),
        (3, "output", REFNUM_T),
        (5, "input", STRING_T),
        (8, "input", ERROR_T),
        (10, "input", I32_T),
        (11, "input", REFNUM_T),
    ]
    gaps: list[Gap] = []
    generate(
        {"file": REFNUM_T},
        {"data": STRING_T},
        [node("rb", 8051, terminals)],
        [("fp:file", "rb:11"), ("rb:2", "fp:data")],
        gaps=gaps,
    )
    assert [g.kind for g in gaps] == ["unknown_default"]
    assert "default (one element) is not written" in gaps[0].reason


def test_a_tag_constant_is_its_text_not_a_quoted_copy_of_it() -> None:
    tag = scalar("Tag")
    generated = generate(
        {}, {"out": tag}, [], [("c", "fp:out")], constants={"c": ("'Dev1/ai0'", tag)}
    )
    assert generated.function().out == "Dev1/ai0"  # type: ignore[operator]


class _FakeLineGrouping:
    def __init__(self, value: int) -> None:
        self.value = value


def test_an_unwired_line_grouping_is_one_channel_for_all_lines(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    source = _generate(
        tmp_path,
        name="DAQmx Create Virtual Channel.vi",
        path=f"{CREATE}/DAQmx Create Channel (DI-Digital Input).vi",
        selector="Digital Input",
        terminals=[
            (0, "input", "task/channels in", REFNUM_T, "task"),
            (4, "output", "task out", REFNUM_T, ""),
            (5, "input", "lines", STRING_T, "lines"),
            (9, "input", "line grouping", I32_T, ""),
        ],
        gaps=gaps,
    )
    assert gaps == []
    calls: list[tuple[str, int]] = []

    class FakeChannels:
        def add_di_chan(self, lines: str, line_grouping: _FakeLineGrouping) -> None:
            calls.append((lines, line_grouping.value))

    class Task:
        di_channels = FakeChannels()

    line = next(ln.strip() for ln in source.splitlines() if ".add_di_chan(" in ln)
    exec(  # noqa: S102
        line,
        {
            "task": Task(),
            "lines": "Dev1/port0/line0:2",
            "LineGrouping": _FakeLineGrouping,
        },
    )
    assert calls == [("Dev1/port0/line0:2", 1)]
