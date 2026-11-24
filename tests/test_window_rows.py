"""The front panel window's rows beyond Title and Window Bounds: the Property Node rows
of ``codegen/nodes/window_rows.py`` (State, Open, Is Frontmost, Panel Bounds, Origin,
Monitor, Behavior, Custom Title, Edit Mode On Open) on the VI's own window and on
another VI's reference, and Display:All Monitors (``codegen/nodes/monitors.py``). Each
case is a handmade diagram whose generated package is imported and run, with a
recording ``live_io`` and ``PanelView``."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.class_graphs import INT, Project, Recipe, pane, wire
from tests.live_io_recorder import Recorder
from tests.test_control_references import property_node
from tests.test_front_panel_bounds import (
    ANONYMOUS_BOUNDS,
    SAVED,
    TYPEDEF_BOUNDS,
    save_bounds,
)
from tests.test_front_panel_reference import (
    BOOL,
    ERROR,
    static_reference,
)

STATE, OPEN, FRONTMOST = 608, 534, 535
PANEL_BOUNDS, ORIGIN, MONITOR, BEHAVIOR = 556, 552, 629, 601
CUSTOM_TITLE, EDIT_MODE = 561, 558
ALL_MONITORS = 34

ORIGIN_TYPE = LVType(
    kind=LVTypeKind.CLUSTER,
    typedef_name="Origin.ctl",
    fields=[ClusterField(name=n, type=INT) for n in ("Horizontal", "Vertical")],
)
ANONYMOUS_ORIGIN = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[ClusterField(name=n, type=INT) for n in ("Horizontal", "Vertical")],
)


def window_vi(rows: list[tuple[int, str, LVType]], target_key: str | None) -> Recipe:
    """A VI with one Property Node of the VI class: a row for each ``(code, "w" or
    "r", type)`` in order, a write row taking the input ``w<i>`` and a read row giving
    the output ``r<i>``; the node is on the static reference of ``target_key``, or
    on the default VI object. Its error out is the output ``error out``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = property_node(
            vi,
            "prop",
            "VI",
            [
                (f"row{i}", (code,), "input" if kind == "w" else "output", lv_type)
                for i, (code, kind, lv_type) in enumerate(rows)
            ],
        )
        nodes: list[GraphNode] = [node]
        wires = [wire(vi, "prop.err_out", "err")]
        if target_key is not None:
            nodes.append(static_reference(vi, target_key))
            wires.append(wire(vi, "ref", "prop.ref"))
        inputs = []
        outputs = []
        written = sum(kind == "w" for _, kind, _ in rows)
        for i, (_, kind, lv_type) in enumerate(rows):
            if kind == "w":
                inputs.append(pane(vi, "input", f"w{i}", lv_type, f"w{i}", len(inputs)))
                wires.append(wire(vi, f"w{i}", f"prop.row{i}"))
            else:
                index = written + len(outputs)
                outputs.append(pane(vi, "output", f"r{i}", lv_type, f"r{i}", index))
                wires.append(wire(vi, f"prop.row{i}", f"r{i}"))
        error_index = written + len(outputs)
        outputs.append(pane(vi, "output", "err", ERROR, "error out", error_index))
        return (
            VIContext(name=vi, inputs=inputs, outputs=outputs, data_flow=wires),
            nodes,
        )

    return build


def own_window(tmp_path: Path, rows: list[tuple[int, str, LVType]]) -> tuple[Any, Any]:
    project = Project(tmp_path)
    project.add_vi("Own", window_vi(rows, None))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    return package, project


def without_writes(recorder: Recorder) -> list[tuple[Any, ...]]:
    return [call_ for call_ in recorder.log if call_[0] != "write"]


def test_the_own_window_is_closed_until_it_opens_and_state_follows_open(
    tmp_path: Path,
) -> None:
    package, _ = own_window(
        tmp_path,
        [(STATE, "r", INT), (OPEN, "w", BOOL), (STATE, "w", INT), (STATE, "r", INT)],
    )
    with package.use() as module:
        own = module("own").own
        assert own.panel.window_state == 2
        recorder = Recorder()
        result = own(w1=True, w2=4, live_io=recorder)
        assert (result.r0, result.r3, result.error_out) == (2, 4, None)
        assert own.panel.window_state == 4
        assert without_writes(recorder) == [
            ("open_panel", False, 1),
            ("window_state", 4),
        ]
        # no live_io: the panel still holds the state
        assert own(w1=True, w2=5).r3 == 5


def test_state_written_on_a_closed_window_is_error_1000_and_tells_no_one(
    tmp_path: Path,
) -> None:
    package, _ = own_window(
        tmp_path,
        [(OPEN, "w", BOOL), (STATE, "w", INT), (STATE, "r", INT)],
    )
    with package.use() as module:
        own = module("own").own
        recorder = Recorder()
        result = own(w0=False, w1=3, live_io=recorder)
        assert result.error_out.code == 1000
        assert result.r2 == 0
        assert [c for c in without_writes(recorder) if c[0] == "window_state"] == []
        assert own.panel.window_state == 2


@pytest.mark.parametrize("state", [0, 2])
def test_state_invalid_or_closed_on_an_open_window_is_error_1(
    tmp_path: Path, state: int
) -> None:
    package, _ = own_window(tmp_path, [(OPEN, "w", BOOL), (STATE, "w", INT)])
    with package.use() as module:
        own = module("own").own
        assert own(w0=True, w1=state).error_out.code == 1
        assert own.panel.window_state == 1


def test_open_false_closes_and_open_reads_whether_it_is_open(tmp_path: Path) -> None:
    package, _ = own_window(
        tmp_path,
        [(OPEN, "r", BOOL), (OPEN, "w", BOOL), (OPEN, "r", BOOL)],
    )
    with package.use() as module:
        own = module("own").own
        recorder = Recorder()
        result = own(w1=True, live_io=recorder)
        assert (result.r0, result.r2) == (False, True)
        assert without_writes(recorder) == [("open_panel", False, 1)]
        recorder = Recorder()
        result = own(w1=False, live_io=recorder)
        assert (result.r0, result.r2) == (True, False)
        assert without_writes(recorder) == [("close_panel",)]


def test_is_frontmost_true_brings_to_front_and_false_does_nothing(
    tmp_path: Path,
) -> None:
    package, _ = own_window(
        tmp_path,
        [(OPEN, "w", BOOL), (FRONTMOST, "w", BOOL), (FRONTMOST, "r", BOOL)],
    )
    with package.use() as module:
        own = module("own").own
        recorder = Recorder()
        assert own(w0=True, w1=True, live_io=recorder).r2 is True
        assert without_writes(recorder) == [
            ("open_panel", False, 1),
            ("bring_to_front",),
            ("is_frontmost", True),
        ]
        recorder = Recorder()
        assert own(w0=True, w1=False, live_io=recorder).r2 is False
        assert ("bring_to_front",) not in recorder.log
        # a function without a live_io that shows windows: the base class answers
        assert own(w0=True, w1=True).r2 is True


CASES = [
    (PANEL_BOUNDS, ANONYMOUS_BOUNDS, SAVED, (1, 2, 3, 4), "panel_bounds"),
    (PANEL_BOUNDS, TYPEDEF_BOUNDS, SAVED, (1, 2, 3, 4), "panel_bounds"),
    (ORIGIN, ANONYMOUS_ORIGIN, (0, 0), (5, 6), "panel_origin"),
    (ORIGIN, ORIGIN_TYPE, (0, 0), (5, 6), "panel_origin"),
    (MONITOR, INT, 0, 2, "window_monitor"),
    (BEHAVIOR, INT, 1, 4, "window_behavior"),
    (EDIT_MODE, BOOL, False, True, None),
]


def as_value(held: Any) -> Any:
    if isinstance(held, SimpleNamespace):
        return tuple(vars(held).values())
    return held


def make(lv_type: LVType, value: Any) -> Any:
    if isinstance(value, tuple) and lv_type.typedef_name:
        return SimpleNamespace(**dict(zip(("a", "b", "c", "d"), value, strict=False)))
    return value


@pytest.mark.parametrize(("code", "lv_type", "default", "written", "tell"), CASES)
def test_a_held_row_reads_its_default_then_the_last_write(
    tmp_path: Path,
    code: int,
    lv_type: LVType,
    default: Any,
    written: Any,
    tell: str | None,
) -> None:
    project = Project(tmp_path)
    key = project.add_vi(
        "Own",
        window_vi(
            [(code, "r", lv_type), (code, "w", lv_type), (code, "r", lv_type)], None
        ),
    )
    save_bounds(project, key, SAVED)
    package = project.generate()
    assert package.gaps == []
    fields = [f.name.lower() for f in lv_type.fields or []]
    with package.use() as module:
        own = module("own").own
        recorder = Recorder()
        value: Any = written
        if lv_type.typedef_name:
            value = SimpleNamespace(**dict(zip(fields, written, strict=True)))
        extra = {} if tell is None else {"live_io": recorder}
        assert ("live_io" in package.sources["own"]) == (tell is not None)
        result = own(w1=value, **extra)
        assert result.error_out is None
        assert as_value(result.r0) == default
        assert as_value(result.r2) == written
        assert as_value(own(w1=value).r0) == written
        expected = [] if tell is None else [(tell, written)]
        assert without_writes(recorder) == expected


def test_custom_title_reads_whether_the_title_differs_and_false_removes_it(
    tmp_path: Path,
) -> None:
    package, _ = own_window(
        tmp_path,
        [
            (CUSTOM_TITLE, "r", BOOL),
            (CUSTOM_TITLE, "w", BOOL),
            (CUSTOM_TITLE, "r", BOOL),
        ],
    )
    with package.use() as module:
        own = module("own").own
        name = own.window_title
        assert own(w1=True).r0 is False
        own.window_title = "A custom title"
        recorder = Recorder()
        result = own(w1=False, live_io=recorder)
        assert (result.r0, result.r2) == (True, False)
        assert own.window_title == name
        assert without_writes(recorder) == [("window_title", name)]
        own.window_title = "Kept"
        assert own(w1=True).r2 is True
        assert own.window_title == "Kept"


def target_window_vi(rows: list[tuple[int, str, LVType]]) -> Recipe:
    return window_vi(rows, "Target")


def reference_runner(
    tmp_path: Path, rows: list[tuple[int, str, LVType]]
) -> tuple[Any, Project]:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", lambda vi: (_target(vi), []))
    runner_key = project.add_vi("Runner", window_vi(rows, target_key))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    return package, project


def _target(vi: str) -> VIContext:
    return VIContext(
        name=vi,
        inputs=[pane(vi, "input", "x", INT, "x", 0)],
        outputs=[pane(vi, "output", "out", INT, "out", 1)],
        data_flow=[wire(vi, "x", "out")],
    )


class Watcher:
    """A ``PanelView`` that records what the panel tells it."""

    def __init__(self, base: type) -> None:
        self.told: list[tuple[Any, ...]] = []

        outer = self

        class Recording(base):  # type: ignore[misc, valid-type]
            def window_changed(self, state: int, active: bool) -> None:
                outer.told.append(("window", state, active))

            def window_property_changed(self, prop: str, value: Any) -> None:
                outer.told.append((prop, value))

            def subpanel_changed(self, control: str, vi: Any) -> None:
                outer.told.append(("subpanel", control, vi))

        self.view = Recording()


def test_rows_on_another_vis_reference_act_on_its_panel_and_tell_its_views(
    tmp_path: Path,
) -> None:
    package, _ = reference_runner(
        tmp_path,
        [
            (OPEN, "w", BOOL),
            (STATE, "w", INT),
            (STATE, "r", INT),
            (FRONTMOST, "w", BOOL),
            (FRONTMOST, "r", BOOL),
            (ORIGIN, "w", ANONYMOUS_ORIGIN),
        ],
    )
    with package.use() as module:
        panel = module("target").target.panel
        watcher = Watcher(module("panels").PanelView)
        panel.attach(watcher.view)
        result = module("runner").runner(w0=True, w1=5, w3=True, w5=(7, 8))
        assert result.error_out is None
        assert (result.r2, result.r4) == (5, True)
        assert panel.window_state == 5
        assert panel.window_value("origin", None) == (7, 8)
        assert watcher.told == [
            ("window", 1, False),
            ("window", 5, False),
            ("window", 5, True),
            ("origin", (7, 8)),
        ]
        # the VI's own function was not given a live_io: nothing of the caller's
        assert "live_io" not in package.sources["runner"]


def test_state_on_a_reference_of_a_closed_window_is_error_1000(tmp_path: Path) -> None:
    package, _ = reference_runner(tmp_path, [(STATE, "w", INT), (STATE, "r", INT)])
    with package.use() as module:
        assert module("runner").runner(w0=3).error_out.code == 1000


def test_a_reference_that_holds_no_vi_is_error_1026(tmp_path: Path) -> None:
    package, _ = reference_runner(tmp_path, [(STATE, "r", INT)])
    with package.use() as module:
        panels = module("panels")
        with pytest.raises(panels.PanelError) as raised:
            panels.panel_of(None)
        assert raised.value.code == 1026


BOUNDS_I16 = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[ClusterField(name=n, type=INT) for n in ("Left", "Top", "Right", "Bottom")],
)
MONITOR_ELEMENT = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name="Bounds", type=BOUNDS_I16),
        ClusterField(name="Depth", type=INT),
    ],
)
MONITORS = LVType(
    kind=LVTypeKind.ARRAY, typedef_name="Monitors.ctl", element_type=MONITOR_ELEMENT
)


def monitors_vi(vi: str) -> tuple[VIContext, list[GraphNode]]:
    node = property_node(
        vi, "prop", "App", [("row0", (ALL_MONITORS,), "output", MONITORS)]
    )
    return (
        VIContext(
            name=vi,
            outputs=[pane(vi, "output", "monitors", MONITORS, "monitors", 0)],
            data_flow=[wire(vi, "prop.row0", "monitors")],
        ),
        [node],
    )


def test_all_monitors_asks_live_io_and_is_empty_without_a_screen(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_vi("Screens", monitors_vi)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        screens = module("screens").screens
        assert screens().monitors == []
        recorder = Recorder()
        assert screens(live_io=recorder).monitors == [(((0, 0, 1920, 1080), 32))]
        assert without_writes(recorder) == [("monitors",)]


def test_a_node_of_the_own_window_with_another_kind_of_row_reads_the_panel(
    tmp_path: Path,
) -> None:
    """Is Frontmost and VI Name on the default VI object in one node: the window row
    is the panel's, the other row the default object's."""
    string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    project = Project(tmp_path)
    project.add_vi("Own", window_vi([(FRONTMOST, "r", BOOL), (500, "r", string)], None))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        own = module("own").own
        assert own().r0 is False
        own.panel.open_window(True)
        assert own().r0 is True
        assert own().r1 == own.vi_name
