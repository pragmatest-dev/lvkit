"""A VI that reads or writes its own front panel while it runs takes the
``live_io`` keyword parameter and calls ``live_io.write`` after every write of
an indicator (``codegen/panel_hooks.py``). Each case is a tiny diagram,
generated and called with a recording ``LiveIO``."""

from __future__ import annotations

import inspect
import re
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lvkit.graph.models import LoopNode
from lvkit.panel.live_io import LiveIO
from tests.case_structure_fixture import FrameSpec
from tests.class_graphs import INT, add, pane, wire
from tests.class_graphs import Project as ClassProject
from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    REQUIRED,
    Package,
    Project,
    Structure,
    Vi,
    increment,
    project,  # noqa: F401
)
from tests.live_io_recorder import Recorder
from tests.structure_diagrams import EVENT_TIMEOUT, event_structure  # noqa: E402

NAME = "Live.vi"
GREATER_OR_EQUAL = 1103


def _body(package: Package) -> str:
    """The statements of the VI's function, after its ``def``."""
    source = package.source
    start = source.index(f"def {NAME.removesuffix('.vi').lower()}(")
    lines = source[start:].splitlines()[1:]
    return textwrap.dedent("\n".join(lines))


def _signature(package: Package) -> list[str]:
    return list(inspect.signature(package.function).parameters)


def _counting_loop(vi: Vi, stop_at: int = 3) -> None:
    """A While Loop with a counter shift register that runs ``stop_at`` times;
    the indicator ``count`` inside the loop shows the counter."""
    count = vi.indicator("count", I32)
    zero = vi.int_constant(0)
    loop = vi.loop("whileLoop", shift_registers=[I32])
    register = loop.shift_registers[0]
    vi.wire(zero, register.left_outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
        limit = vi.int_constant(stop_at)
        done = vi.primitive(
            GREATER_OR_EQUAL,
            [("done", 0, BOOL, True), ("y", 1, I32, False), ("x", 2, I32, False)],
        )
    vi.place_inside(count, loop.scopes[0])
    vi.wire(register.left_face, inc["in"])
    vi.wire(inc["out"], register.right_face, count, done["x"])
    vi.wire(limit, done["y"])
    vi.wire(done["done"], loop.faces[0])


def test_a_while_loop_indicator_is_updated_on_every_iteration(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _counting_loop(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["live_io"]
    assert "def live(*, live_io: LiveIO=NO_LIVE_IO) -> LiveResult:" in package.source
    assert "from .live_io import LiveIO, NO_LIVE_IO" in package.source
    assert "Callable" not in package.source
    assert _body(package) == textwrap.dedent(
        """\
        count = 0
        state = 0
        while True:
            x1 = state + 1
            count = x1
            live_io.write(LiveValue.COUNT, count)
            state_updated = x1
            state = state_updated
            if x1 >= 3:
                break
        return LiveResult(count=count)"""
    )
    recorder = Recorder()
    assert package.function(live_io=recorder).count == 3
    assert recorder.calls == [("count", 1), ("count", 2), ("count", 3)]
    assert package.function().count == 3
    assert package.function(live_io=LiveIO()).count == 3


def _for_loop_with_outer_indicator(vi: Vi) -> None:
    n = vi.control("n", I32, REQUIRED)
    inner = vi.indicator("inner", I32)
    outer = vi.indicator("outer", I32)
    zero = vi.int_constant(0)
    loop = vi.loop("forLoop", shift_registers=[I32])
    register = loop.shift_registers[0]
    vi.wire(n, loop.selector)
    vi.wire(zero, register.left_outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
    vi.place_inside(inner, loop.scopes[0])
    vi.wire(register.left_face, inc["in"])
    vi.wire(inc["out"], register.right_face, inner)
    vi.wire(register.right_outer, outer)


def test_an_indicator_outside_the_loop_is_updated_too_after_the_loop(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _for_loop_with_outer_indicator(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["n", "live_io"]
    # The wire into the loop's output tunnel is named from its uid.
    body = re.sub(r"outer_\d+", "step", _body(package))
    assert body == textwrap.dedent(
        """\
        inner = 0
        outer = 0
        state = 0
        for i in range(n):
            step = state + 1
            inner = step
            live_io.write(LiveValue.INNER, inner)
            state_updated = step
            state = state_updated
        outer = state
        live_io.write(LiveValue.OUTER, outer)
        return LiveResult(inner=inner, outer=outer)"""
    )
    recorder = Recorder()
    result = package.function(n=3, live_io=recorder)
    assert (result.inner, result.outer) == (3, 3)
    assert recorder.calls == [("inner", 1), ("inner", 2), ("inner", 3), ("outer", 3)]
    recorder = Recorder()
    package.function(n=0, live_io=recorder)
    assert recorder.calls == [("outer", 0)]
    assert package.function(n=2).outer == 2


def test_a_cluster_indicator_is_passed_whole(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    cluster = vi.control("cluster", ERR, REQUIRED)
    shown = vi.indicator("shown", ERR)
    loop = vi.loop("forLoop", tunnels=[(ERR, False)])
    vi.wire(n, loop.selector)
    vi.wire(cluster, loop.tunnels[0].outer)
    vi.place_inside(shown, loop.scopes[0])
    vi.wire(loop.tunnels[0].faces[0], shown)
    project.add(vi)
    package = project.generate(NAME)
    recorder = Recorder()
    value = object()
    package.function(n=2, cluster=value, live_io=recorder)
    assert [name for name, _ in recorder.calls] == ["shown", "shown"]
    assert all(passed is value for _, passed in recorder.calls)


def test_a_control_and_an_indicator_with_one_label_get_distinct_names(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    control = vi.control("value", I32, REQUIRED)
    indicator = vi.indicator("value", I32)
    other = vi.indicator("other", I32)
    loop = vi.loop("forLoop", tunnels=[(I32, False)])
    vi.wire(n, loop.selector)
    vi.wire(control, loop.tunnels[0].outer)
    vi.place_inside(indicator, loop.scopes[0])
    vi.place_inside(other, loop.scopes[0])
    vi.wire(loop.tunnels[0].faces[0], indicator, other)
    project.add(vi)
    package = project.generate(NAME)
    # The control keeps its parameter name; the indicator, whose result field is
    # also ``value``, is told apart from it.
    assert _signature(package) == ["n", "value", "live_io"]
    recorder = Recorder()
    result = package.function(n=1, value=5, live_io=recorder)
    assert (result.value, result.other) == (5, 5)
    names = [name for name, _ in recorder.calls]
    assert sorted(names) == ["other", "value_2"]
    assert "value" not in names


def test_a_loop_without_a_front_panel_terminal_has_no_hook(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    outer = vi.indicator("outer", I32)
    zero = vi.int_constant(0)
    loop = vi.loop("forLoop", shift_registers=[I32])
    register = loop.shift_registers[0]
    vi.wire(n, loop.selector)
    vi.wire(zero, register.left_outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
    vi.wire(register.left_face, inc["in"])
    vi.wire(inc["out"], register.right_face)
    vi.wire(register.right_outer, outer)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["n"]
    assert "live_io" not in package.source
    assert "Callable" not in package.source
    assert _body(package) == textwrap.dedent(
        """\
        state = 0
        for i in range(n):
            state_updated = state + 1
            state = state_updated
        return LiveResult(outer=state)"""
    )
    assert package.function(n=4).outer == 4


def test_a_vi_without_a_loop_has_no_hook(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    out = vi.indicator("out", I32)
    inc = increment(vi)
    vi.wire(x, inc["in"])
    vi.wire(inc["out"], out)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["x"]
    assert "live_io" not in package.source
    assert _body(package) == "return LiveResult(out=x + 1)"
    assert package.function(x=4).out == 5


def test_a_local_variable_of_an_indicator_is_a_hook_without_a_loop(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    count = vi.indicator("count", I32)
    read = vi.local_variable(count)
    inc = increment(vi)
    vi.wire(read, inc["in"])
    vi.wire(inc["out"], count)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["live_io"]
    recorder = Recorder()
    assert package.function(live_io=recorder).count == 1
    assert recorder.calls == [("count", 1)]


def test_the_views_of_a_hooked_vi_do_not_show_the_hooks(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _counting_loop(vi)
    project.add(vi)
    before = project.views(NAME)
    project.generate(NAME)
    after = project.views(NAME)
    for view in ("lvnet", "terse", "describe", "json"):
        assert getattr(after, view) == getattr(before, view)
        assert "live_io" not in str(getattr(after, view))


def test_a_hooked_class_method_takes_the_hooks_after_its_own_keywords(
    tmp_path: Path,
) -> None:
    """A class method whose indicator sits in a For Loop."""

    def method(vi: str) -> tuple[Any, list[Any]]:
        from lvkit.graph.models import VIContext

        loop = LoopNode(
            id=f"{vi}:for",
            vi_path=vi,
            node_type="forLoop",
            loop_type="forLoop",
            terminals=[],
        )
        out = pane(vi, "output", "out", INT, "out", 1)
        out.parent = loop.id
        summed = add(vi, "sum")
        summed.parent = loop.id
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "n", INT, "n", 0)],
                outputs=[out],
                data_flow=[
                    wire(vi, "n", "sum.x"),
                    wire(vi, "n", "sum.y"),
                    wire(vi, "sum.sum", "out"),
                ],
            ),
            [loop, summed],
        )

    classes = ClassProject(tmp_path)
    classes.add_class("Counter", [])
    classes.add_method("Counter", "Run", method, static=True)
    package = classes.generate()
    assert package.gaps == []
    source = package.sources["counter"]
    assert "def run(n: int, *, live_io: LiveIO=NO_LIVE_IO)" in source
    # The enum is the class's and the method's name, at module level next to the
    # class, before it.
    assert "\nclass CounterRunValue(str, Enum):\n    N = 'n'\n    OUT = 'out'\n" in (
        source
    )
    assert source.index("class CounterRunValue") < source.index("class Counter:")
    assert "live_io.write(CounterRunValue.OUT, out)" in source
    assert "from enum import Enum" in source
    with package.use() as module:
        recorder = Recorder()
        result = module("counter").Counter.run(n=3, live_io=recorder)
        assert result.out == 6
        assert recorder.calls[0][0] is module("counter").CounterRunValue.OUT
        # The loop's count is not wired: it runs its default of 10 times.
        assert recorder.calls == [("out", 6)] * 10


def _stop_loop(vi: Vi) -> Structure:
    """A While Loop with the Stop control ``stop`` inside it, wired to the stop
    terminal, and the indicator ``count`` showing the iteration's counter."""
    stop = vi.control("stop", BOOL, REQUIRED)
    count = vi.indicator("count", I32)
    zero = vi.int_constant(0)
    loop = vi.loop("whileLoop", shift_registers=[I32])
    register = loop.shift_registers[0]
    vi.wire(zero, register.left_outer)
    with vi.inside(loop.scopes[0]):
        inc = increment(vi)
    vi.place_inside(stop, loop.scopes[0])
    vi.place_inside(count, loop.scopes[0])
    vi.wire(register.left_face, inc["in"])
    vi.wire(inc["out"], register.right_face, count)
    vi.wire(stop, loop.faces[0])
    return loop


def test_a_control_inside_a_while_loop_is_read_again_every_iteration(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _stop_loop(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["stop", "live_io"]
    assert _body(package) == textwrap.dedent(
        """\
        count = 0
        state = 0
        while True:
            stop = live_io.read(LiveValue.STOP, stop)
            x1 = state + 1
            count = x1
            live_io.write(LiveValue.COUNT, count)
            state_updated = x1
            state = state_updated
            if stop:
                break
        return LiveResult(count=count)"""
    )
    recorder = Recorder(False, False, True)
    result = package.function(stop=False, live_io=recorder)
    assert result.count == 3
    assert recorder.reads == [("stop", False)] * 3
    assert recorder.calls == [("count", 1), ("count", 2), ("count", 3)]
    # With no live_io the loop is the one it is without one: it stops at once.
    assert package.function(stop=True).count == 1
    assert package.function(stop=True, live_io=LiveIO()).count == 1


def _loop_with_a_control_wired_to_an_indicator(vi: Vi) -> None:
    """``_stop_loop`` plus the control ``offset`` inside the loop, wired straight
    to the indicator ``seen`` inside the loop."""
    loop = _stop_loop(vi)
    offset = vi.control("offset", I32, REQUIRED)
    seen = vi.indicator("seen", I32)
    vi.place_inside(offset, loop.scopes[0])
    vi.place_inside(seen, loop.scopes[0])
    vi.wire(offset, seen)


def test_a_control_wired_to_an_indicator_in_a_loop_reaches_it_every_pass(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _loop_with_a_control_wired_to_an_indicator(vi)
    project.add(vi)
    package = project.generate(NAME)
    passes = iter([(1, False), (2, False), (3, True)])
    current: dict[str, Any] = {}

    def answer(name: str, value: Any) -> Any:
        if name == "stop":
            current["offset"], stop = next(passes)
            return stop
        return current["offset"]

    recorder = Recorder(answer=answer)
    result = package.function(stop=False, offset=0, live_io=recorder)
    assert [c for c in recorder.calls if c[0] == "seen"] == [
        ("seen", 1),
        ("seen", 2),
        ("seen", 3),
    ]
    assert result.seen == 3
    # Without hooks the indicator holds the last pass's value too.
    assert package.function(stop=True, offset=7).seen == 7


def _control_in_a_case_in_a_for_loop(vi: Vi) -> None:
    """``gain`` sits in the True frame of a Case inside a For Loop of ``n``
    passes; the Case gives ``gain`` (or 0) to the indicator ``seen`` inside the
    loop."""
    n = vi.control("n", I32, REQUIRED)
    gain = vi.control("gain", I32, REQUIRED)
    flag = vi.control("flag", BOOL, REQUIRED)
    seen = vi.indicator("seen", I32)
    loop = vi.loop("forLoop")
    vi.wire(n, loop.selector)
    with vi.inside(loop.scopes[0]):
        case = vi.case(
            "boolean",
            BOOL,
            [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))],
            default_frame=None,
            tunnels=[(I32, True)],
        )
        vi.wire(flag, case.selector)
        with vi.inside(case.scopes[0]):
            zero = vi.int_constant(0)
        vi.wire(zero, case.tunnels[0].faces[0])
        vi.place_inside(gain, case.scopes[1])
        vi.wire(gain, case.tunnels[0].faces[1])
    vi.place_inside(seen, loop.scopes[0])
    vi.wire(case.tunnels[0].outer, seen)


def test_a_control_in_a_case_in_a_loop_is_read_at_the_start_of_that_frame(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _control_in_a_case_in_a_for_loop(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["n", "gain", "flag", "live_io"]
    # The read is the first statement of the frame that holds the terminal, so
    # it happens on the passes that run that frame only.
    assert _body(package) == textwrap.dedent(
        """\
        seen = 0
        for i in range(n):
            if flag:
                gain = live_io.read(LiveValue.GAIN, gain)
                seen_2 = gain
            else:
                seen_2 = 0
            seen = seen_2
            live_io.write(LiveValue.SEEN, seen)
        return LiveResult(seen=seen)"""
    )
    recorder = Recorder(10, 20, 30)
    result = package.function(n=3, gain=1, flag=True, live_io=recorder)
    assert result.seen == 30
    assert recorder.reads == [("gain", 1), ("gain", 10), ("gain", 20)]
    assert recorder.calls == [("seen", 10), ("seen", 20), ("seen", 30)]
    recorder = Recorder()
    assert package.function(n=3, gain=1, flag=False, live_io=recorder).seen == 0
    assert recorder.reads == []
    assert package.function(n=3, gain=7, flag=True).seen == 7


def test_a_control_in_an_event_structure_hooks_the_vi_and_takes_events(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    stop = vi.control("stop", BOOL, REQUIRED)
    scope = vi._new_scope()
    vi.place_inside(stop, scope)
    uid = vi.next_uid()

    def xml() -> str:
        return event_structure(uid, [], [vi._diagram_xml(scope)], [EVENT_TIMEOUT])

    vi._place(Structure(0, [], [scope], [], xml=xml))
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["stop", "live_io"]
    assert "live_io: LiveIO=NO_LIVE_IO" in package.source
    assert package.gaps == []


def _off_pane_control(vi: Vi, *, wired: bool) -> None:
    """The control ``bias`` is not on the connector pane; the diagram adds 1 to
    it (``wired``), or never uses it."""
    bias = vi.control("bias", I32, rule=None)
    x = vi.control("x", I32, REQUIRED)
    out = vi.indicator("out", I32)
    inc = increment(vi)
    vi.wire(bias if wired else x, inc["in"])
    vi.wire(inc["out"], out)


def test_an_off_pane_control_the_diagram_reads_is_read_once_at_entry(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _off_pane_control(vi, wired=True)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["x", "live_io"]
    # After the docstring that lists the off-pane control.
    statements = textwrap.dedent(_body(package).rsplit('"""', 1)[1]).strip()
    assert statements == textwrap.dedent(
        """\
        bias = live_io.read(LiveValue.BIAS, 0)
        out = bias + 1
        live_io.write(LiveValue.OUT, out)
        return LiveResult(out=out)"""
    )
    recorder = Recorder(41)
    result = package.function(x=0, live_io=recorder)
    assert result.out == 42
    assert recorder.reads == [("bias", 0)]
    assert recorder.calls == [("out", 42)]
    # Without a hook the saved default is used.
    assert package.function(x=0).out == 1


def test_an_off_pane_control_nothing_reads_leaves_the_vi_unhooked(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _off_pane_control(vi, wired=False)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["x"]
    assert "live_io" not in package.source
    assert "live_io" not in package.source
    assert package.function(x=4).out == 5


def test_a_control_read_only_outside_loops_has_no_read_line(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _for_loop_with_outer_indicator(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["n", "live_io"]
    assert "live_io.read(" not in package.source


def _wired_off_pane_control(vi: Vi) -> None:
    _off_pane_control(vi, wired=True)


@pytest.mark.parametrize(
    "build",
    [_stop_loop, _control_in_a_case_in_a_for_loop, _wired_off_pane_control],
)
def test_the_views_of_a_vi_with_reads_do_not_show_the_hooks(
    build: Callable[[Vi], None],
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    build(vi)
    project.add(vi)
    before = project.views(NAME)
    project.generate(NAME)
    after = project.views(NAME)
    for view in ("lvnet", "terse", "describe", "json"):
        assert getattr(after, view) == getattr(before, view)
        assert "live_io" not in str(getattr(after, view))


# -- the names are one generated enum per hooked VI --------------------------


def test_the_names_are_members_of_an_enum_the_module_defines(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _stop_loop(vi)
    project.add(vi)
    package = project.generate(NAME)
    source = package.source
    assert "from enum import Enum" in source
    assert (
        "class LiveValue(str, Enum):\n    STOP = 'stop'\n    COUNT = 'count'\n"
    ) in source
    # The enum comes before the function and the result class before the enum.
    assert source.index("class LiveResult") < source.index("class LiveValue")
    assert source.index("class LiveValue") < source.index("def live(")
    assert "def live(stop: bool, *, live_io: LiveIO=NO_LIVE_IO)" in source
    value = package.module.LiveValue
    assert [(m.name, m.value) for m in value] == [("STOP", "stop"), ("COUNT", "count")]
    assert issubclass(value, str)
    recorder = Recorder(False, True)
    package.function(stop=False, live_io=recorder)
    assert recorder.reads[0][0] is value.STOP
    assert recorder.calls[0][0] is value.COUNT
    assert recorder.calls[0][0] == "count"
    assert recorder.reads == [("stop", False), ("stop", False)]


def test_a_vi_without_hooks_has_no_enum_and_imports_no_enum(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    out = vi.indicator("out", I32)
    inc = increment(vi)
    vi.wire(x, inc["in"])
    vi.wire(inc["out"], out)
    project.add(vi)
    source = project.generate(NAME).source
    assert "Enum" not in source
    assert "Value" not in source


def test_an_enum_name_is_distinct_from_the_other_names_of_the_module(
    project: Project,  # noqa: F811
) -> None:
    """A VI named ``Live Value`` has the function ``live_value``, whose enum would
    be ``LiveValueValue``; a control named like the enum does not matter, and the
    enum never takes the name of the result class."""
    vi = Vi("Live Value.vi")
    _counting_loop(vi)
    project.add(vi)
    package = project.generate("Live Value.vi")
    assert "class LiveValueValue(str, Enum):" in package.source
    assert "class LiveValueResult" in package.source


# -- local variables of controls ---------------------------------------------


def _write_then_read_a_control(vi: Vi) -> None:
    """Constant 5 is written to the control ``n`` by a Local Variable, and a
    Local Variable read of ``n`` shows it in the indicator ``out``."""
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    five = vi.int_constant(5)
    write = vi.local_variable(n)
    read = vi.local_variable(n)
    vi.wire(five, write)
    vi.wire(read, out)


def test_a_local_variable_write_of_a_control_assigns_it(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _write_then_read_a_control(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert _signature(package) == ["n", "live_io"]
    assert _body(package) == textwrap.dedent(
        """\
        out = 0
        n = 5
        live_io.write(LiveValue.N, n)
        out = n
        live_io.write(LiveValue.OUT, out)
        return LiveResult(out=out)"""
    )
    recorder = Recorder()
    assert package.function(n=1, live_io=recorder).out == 5
    assert recorder.calls == [("n", 5), ("out", 5)]
    assert package.function(n=1).out == 5


def test_a_write_and_a_read_no_wire_orders_follow_the_order_the_graph_lists_them(
    project: Project,  # noqa: F811
) -> None:
    """LabVIEW orders a Local Variable write and read of one control by dataflow
    only, and nothing wires these two: lvkit does not invent an order, so each
    runs where the diagram lists it."""
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    five = vi.int_constant(5)
    read = vi.local_variable(n)
    write = vi.local_variable(n)
    vi.wire(five, write)
    vi.wire(read, out)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        out = n
        live_io.write(LiveValue.OUT, out)
        n = 5
        live_io.write(LiveValue.N, n)
        return LiveResult(out=out)"""
    )
    assert package.function(n=1).out == 1


def _write_then_read_in_a_while_loop(vi: Vi) -> None:
    """Constant 7 is written to the control ``n`` by a Local Variable; inside a
    While Loop (one pass) a Local Variable read of ``n`` shows in ``seen``."""
    n = vi.control("n", I32, REQUIRED)
    seen = vi.indicator("seen", I32)
    seven = vi.int_constant(7)
    write = vi.local_variable(n)
    vi.wire(seven, write)
    loop = vi.loop("whileLoop")
    with vi.inside(loop.scopes[0]):
        read = vi.local_variable(n)
        stop = vi.bool_constant(True)
    vi.place_inside(seen, loop.scopes[0])
    vi.wire(read, seen)
    vi.wire(stop, loop.faces[0])


def test_a_read_in_a_loop_follows_the_write_and_asks_for_the_value(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _write_then_read_in_a_while_loop(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert _body(package) == textwrap.dedent(
        """\
        seen = 0
        n = 7
        live_io.write(LiveValue.N, n)
        while True:
            n = live_io.read(LiveValue.N, n)
            seen = n
            live_io.write(LiveValue.SEEN, seen)
            break
        return LiveResult(seen=seen)"""
    )
    recorder = Recorder(40)
    result = package.function(n=1, live_io=recorder)
    assert result.seen == 40
    assert recorder.log == [
        ("write", "n", 7),
        ("read", "n", 7),
        ("write", "seen", 40),
    ]
    assert package.function(n=1).seen == 7


def test_a_read_of_a_control_nothing_writes_outside_a_loop_is_the_parameter(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    read = vi.local_variable(n)
    inc = increment(vi)
    vi.wire(read, inc["in"])
    vi.wire(inc["out"], out)
    project.add(vi)
    package = project.generate(NAME)
    assert "live_io.read(" not in package.source
    assert package.function(n=4).out == 5


def test_a_local_variable_read_of_an_indicator_is_the_indicator_variable(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    count = vi.indicator("count", I32)
    read = vi.local_variable(count)
    inc = increment(vi)
    vi.wire(read, inc["in"])
    vi.wire(inc["out"], count)
    project.add(vi)
    package = project.generate(NAME)
    assert re.sub(r"_live_\d+", "_live", _body(package)) == textwrap.dedent(
        """\
        global _lv_indicator_live
        count = _lv_indicator_live
        x1 = count + 1
        count = x1
        live_io.write(LiveValue.COUNT, count)
        _lv_indicator_live = count
        return LiveResult(count=count)"""
    )


@pytest.mark.parametrize(
    "build", [_write_then_read_a_control, _write_then_read_in_a_while_loop]
)
def test_the_views_of_a_vi_with_local_variables_of_controls_are_unchanged(
    build: Callable[[Vi], None],
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    build(vi)
    project.add(vi)
    before = project.views(NAME)
    package = project.generate(NAME)
    after = project.views(NAME)
    assert "LiveValue" in package.source
    for view in ("lvnet", "terse", "describe", "json"):
        assert getattr(after, view) == getattr(before, view)
        for hook in ("live_io", "LiveValue", "Enum"):
            assert hook not in str(getattr(after, view))
