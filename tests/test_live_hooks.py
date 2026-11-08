"""A VI that reads or writes its own front panel while it runs takes the
``on_update`` and ``on_read`` keyword parameters and calls ``on_update`` after
every write of an indicator (``codegen/panel_hooks.py``). Each case is a tiny
diagram, generated and called with a recording ``on_update``."""

from __future__ import annotations

import inspect
import re
import textwrap
from pathlib import Path
from typing import Any

from lvkit.graph.models import LoopNode
from tests.class_graphs import INT, add, pane, wire
from tests.class_graphs import Project as ClassProject
from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    increment,
    project,  # noqa: F401
)

NAME = "Live.vi"
GREATER_OR_EQUAL = 1103


class Recorder:
    """An ``on_update`` that records its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def __call__(self, name: str, value: Any) -> None:
        self.calls.append((name, value))


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
    assert _signature(package) == ["on_update", "on_read"]
    assert (
        "def live(*, on_update: Callable[[str, Any], None] | None=None, "
        "on_read: Callable[[str, Any], Any] | None=None) -> LiveResult:"
    ) in package.source
    assert "from collections.abc import Callable" in package.source
    assert _body(package) == textwrap.dedent(
        """\
        count = 0
        state = 0
        while True:
            x1 = state + 1
            count = x1
            if on_update is not None:
                on_update('count', count)
            state_updated = x1
            state = state_updated
            if x1 >= 3:
                break
        return LiveResult(count=count)"""
    )
    recorder = Recorder()
    assert package.function(on_update=recorder).count == 3
    assert recorder.calls == [("count", 1), ("count", 2), ("count", 3)]
    assert package.function().count == 3
    assert package.function(on_update=None, on_read=None).count == 3


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
    assert _signature(package) == ["n", "on_update", "on_read"]
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
            if on_update is not None:
                on_update('inner', inner)
            state_updated = step
            state = state_updated
        outer = state
        if on_update is not None:
            on_update('outer', outer)
        return LiveResult(inner=inner, outer=outer)"""
    )
    recorder = Recorder()
    result = package.function(n=3, on_update=recorder)
    assert (result.inner, result.outer) == (3, 3)
    assert recorder.calls == [("inner", 1), ("inner", 2), ("inner", 3), ("outer", 3)]
    recorder = Recorder()
    package.function(n=0, on_update=recorder)
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
    package.function(n=2, cluster=value, on_update=recorder)
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
    assert _signature(package) == ["n", "value", "on_update", "on_read"]
    recorder = Recorder()
    result = package.function(n=1, value=5, on_update=recorder)
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
    assert "on_update" not in package.source
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
    assert "on_update" not in package.source
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
    assert _signature(package) == ["on_update", "on_read"]
    recorder = Recorder()
    assert package.function(on_update=recorder).count == 1
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
        assert "on_update" not in str(getattr(after, view))
        assert "on_read" not in str(getattr(after, view))


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
    assert (
        "def run(n: int, *, on_update: Callable[[str, Any], None] | None=None, "
        "on_read: Callable[[str, Any], Any] | None=None)"
    ) in source
    with package.use() as module:
        recorder = Recorder()
        result = module("counter").Counter.run(n=3, on_update=recorder)
        assert result.out == 6
        # The loop's count is not wired: it runs its default of 10 times.
        assert recorder.calls == [("out", 6)] * 10
