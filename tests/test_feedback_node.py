"""A Feedback Node generates Python (``codegen/nodes/feedback.py``): the value it
stores is a local variable when its initializer sits on a loop's border and a
module variable kept between calls otherwise. Each case is a tiny diagram,
generated and called."""

from __future__ import annotations

import pytest

from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Project,
    Vi,
    increment,
    json_body,
    project,  # noqa: F401
)

NAME = "Fb.vi"
ADD = 1050
ARRAY = '<TypeDesc Type="Array"><Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'


def _array(element: int) -> str:
    return f'{ARRAY}<TypeDesc TypeID="{element}" /></TypeDesc>'


def _running_sum(
    project: Project,  # noqa: F811
    *,
    on_border: bool,
    initializer: bool = True,
    start: int | None = 0,
    start_control: bool = False,
) -> None:
    """A While Loop that adds each element of ``values`` to a Feedback Node and
    stops after ``stops`` runs out; ``total`` is the last sum. The node's
    initializer is ``start`` (a constant) on the loop's border or on the node, a
    control ``start`` when ``start_control``, or unwired when ``initializer`` is
    False."""
    vi = Vi(NAME)
    values_type = vi.add_type(_array(I32))
    stops_type = vi.add_type(_array(BOOL))
    values = vi.control("values", values_type, REQUIRED)
    stops = vi.control("stops", stops_type, REQUIRED)
    first = vi.control("start", I32, REQUIRED) if start_control else None
    total = vi.indicator("total", I32)
    link = vi.next_uid()
    loop = vi.loop(
        "whileLoop",
        tunnels=[(I32, True)],
        indexing_inputs=[(values_type, I32), (stops_type, BOOL)],
        feedback_initializers=[(I32, link)] if on_border else [],
    )
    out, value_in, stop_in = loop.tunnels
    with vi.inside(loop.scopes[0]):
        fb = vi.feedback_node(
            I32, initializer=initializer and not on_border, link=link
        )
        add = vi.primitive(
            ADD,
            [("sum", 0, I32, True), ("y", 1, I32, False), ("x", 2, I32, False)],
        )
    vi.wire(values, value_in.outer)
    vi.wire(stops, stop_in.outer)
    vi.wire(stop_in.faces[0], loop.faces[0])
    vi.wire(fb["read"], add["x"])
    vi.wire(value_in.faces[0], add["y"])
    vi.wire(add["sum"], fb["write"], out.faces[0])
    vi.wire(out.outer, total)
    if initializer:
        source = first if first is not None else None
        if source is None:
            vi.scope = vi.root
            source = vi.int_constant(start or 0)
        target = loop.initializers[0] if on_border else fb["init"]
        vi.wire(source, target)
    project.add(vi)


def _call(package, values: list[int]) -> int:
    stops = [False] * (len(values) - 1) + [True]
    return package.function(values=values, stops=stops).total


@pytest.mark.parametrize(
    ("count", "total"), [(1, 1), (2, 3), (3, 6), (4, 10), (5, 15)]
)
def test_a_running_sum_starts_from_the_initializer_on_the_loop_border(
    project: Project,  # noqa: F811
    count: int,
    total: int,
) -> None:
    _running_sum(project, on_border=True)
    package = project.generate(NAME)
    assert _call(package, list(range(1, count + 1))) == total


def test_an_initializer_on_the_loop_border_starts_every_execution_of_the_loop(
    project: Project,  # noqa: F811
) -> None:
    _running_sum(project, on_border=True, start=100)
    package = project.generate(NAME)
    assert [_call(package, [1, 2, 3]) for _ in range(3)] == [106, 106, 106]
    assert "global" not in package.source


def test_a_wired_initializer_on_the_node_is_used_on_the_first_call_only(
    project: Project,  # noqa: F811
) -> None:
    _running_sum(project, on_border=False, start=10)
    package = project.generate(NAME)
    assert [_call(package, [1, 2]) for _ in range(3)] == [13, 16, 19]


def test_a_node_without_an_initializer_continues_from_the_call_before(
    project: Project,  # noqa: F811
) -> None:
    _running_sum(project, on_border=False, initializer=False)
    package = project.generate(NAME)
    assert [_call(package, [1, 2, 3]) for _ in range(2)] == [6, 12]


def test_an_initializer_wired_to_a_control_is_read_on_the_first_call_only(
    project: Project,  # noqa: F811
) -> None:
    _running_sum(project, on_border=False, start_control=True)
    package = project.generate(NAME)
    stops = [False, True]
    first = package.function(values=[1, 2], stops=stops, start=50).total
    second = package.function(values=[1, 2], stops=stops, start=900).total
    assert (first, second) == (53, 56)


def test_a_loop_border_initializer_wired_to_a_control_is_read_every_time(
    project: Project,  # noqa: F811
) -> None:
    _running_sum(project, on_border=True, start_control=True)
    package = project.generate(NAME)
    stops = [False, True]
    results = [
        package.function(values=[1, 2], stops=stops, start=s).total for s in (5, 70)
    ]
    assert results == [8, 73]


def _counter(project: Project, **fb_options: object) -> Vi:  # noqa: F811
    """``count`` is a Feedback Node's value plus one, outside any loop."""
    vi = Vi(NAME)
    count = vi.indicator("count", I32)
    fb = vi.feedback_node(I32, **fb_options)  # type: ignore[arg-type]
    inc = increment(vi)
    vi.wire(fb["read"], inc["in"])
    vi.wire(inc["out"], fb["write"], count)
    if "init" in fb:
        vi.wire(vi.int_constant(40), fb["init"])
    project.add(vi)
    return vi


def test_a_node_outside_a_loop_counts_the_calls(project: Project) -> None:  # noqa: F811
    _counter(project, initializer=False)
    package = project.generate(NAME)
    assert [package.function().count for _ in range(4)] == [1, 2, 3, 4]


def test_a_node_outside_a_loop_starts_from_its_initializer(
    project: Project,  # noqa: F811
) -> None:
    _counter(project, initializer=True)
    package = project.generate(NAME)
    assert [package.function().count for _ in range(3)] == [41, 42, 43]


def test_the_write_runs_after_the_read_when_nothing_wires_them_together(
    project: Project,  # noqa: F811
) -> None:
    """The write side's value is a constant: no wire orders it after the read, and
    the module variable is declared by the read."""
    vi = Vi(NAME)
    seen = vi.indicator("seen", I32)
    fb = vi.feedback_node(I32, initializer=False)
    vi.wire(fb["read"], seen)
    vi.wire(vi.int_constant(7), fb["write"])
    project.add(vi)
    package = project.generate(NAME)
    assert [package.function().seen for _ in range(3)] == [0, 7, 7]


def test_a_delay_of_two_is_a_gap(project: Project) -> None:  # noqa: F811
    _counter(project, initializer=False, delay=2)
    package = project.generate(NAME)
    with pytest.raises(NotImplementedError, match="delay of 2"):
        package.function()


def test_an_enable_terminal_is_a_gap(project: Project) -> None:  # noqa: F811
    _counter(project, initializer=False, extra_terminal=True)
    package = project.generate(NAME)
    with pytest.raises(NotImplementedError, match="conditionalFeedbackDCO"):
        package.function()


def test_the_views_show_an_initializer_on_the_loop_border(
    project: Project,  # noqa: F811
) -> None:
    """lvnet and describe's netlist read the initializer from the loop's border
    terminal, as they read one wired to the node."""
    _running_sum(project, on_border=True, start=3)
    views = project.views(NAME)
    assert "feedback-node fb0 (1 iteration) :\n" in views.lvnet
    assert "        init = 3\n" in views.lvnet
    assert "uninitialized" not in views.lvnet
    assert json_body(views)


def test_the_views_show_the_initializer_on_the_loop_border(
    project: Project,  # noqa: F811
) -> None:
    """lvnet and JSON give the node the value wired to the loop's initializer,
    as they do one wired to the node."""
    _running_sum(project, on_border=True, start=5)
    views = project.views(NAME)
    assert "feedback-node fb0 (1 iteration) :\n" in views.diagram
    assert "        init = 5\n" in views.diagram
    assert "uninitialized" not in views.diagram


def test_the_views_show_an_unwired_loop_border_initializer_as_uninitialized(
    project: Project,  # noqa: F811
) -> None:
    _running_sum(project, on_border=True, initializer=False)
    views = project.views(NAME)
    assert "feedback-node fb0 (1 iteration, uninitialized) :" in views.diagram
