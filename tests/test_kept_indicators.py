"""An indicator a Local Variable reads keeps its value from one call to the next
(``codegen/local_indicators.py``): a module variable holds it. Each case is a
tiny diagram, generated and called several times."""

from __future__ import annotations

from collections.abc import Callable

from tests.case_structure_fixture import FrameSpec
from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Project,
    Vi,
    increment,
    project,  # noqa: F401
)

NAME = "Count.vi"
NOTE = "A preallocated clone keeps this value for each call site in LabVIEW"
FRAMES = [FrameSpec(ranges=((0, 0),)), FrameSpec(ranges=((1, 1),))]


def _counter(project: Project, configure: Callable[[Vi], None] | None = None) -> None:  # noqa: F811
    """``count = (Local Variable read of count) + 1`` on the VI's diagram."""
    vi = Vi(NAME)
    count = vi.indicator("count", I32)
    read = vi.local_variable(count)
    inc = increment(vi)
    vi.wire(read, inc["in"])
    vi.wire(inc["out"], count)
    if configure is not None:
        configure(vi)
    project.add(vi)


def test_a_local_read_sees_the_value_the_previous_call_left(
    project: Project,  # noqa: F811
) -> None:
    _counter(project)
    package = project.generate(NAME)
    assert NOTE not in package.source
    assert [package.function().count for _ in range(3)] == [1, 2, 3]


def test_clear_indicators_when_called_starts_from_the_default(
    project: Project,  # noqa: F811
) -> None:
    def clears(vi: Vi) -> None:
        vi.execution2["ClearIndMask"] = "1"

    _counter(project, clears)
    package = project.generate(NAME)
    assert [package.function().count for _ in range(3)] == [1, 1, 1]


def test_an_unwritten_pane_indicator_returns_its_default_and_keeps_it(
    project: Project,  # noqa: F811
) -> None:
    """The True frame writes ``count``; a call that takes the False frame
    returns the default, and the next call counts on from it."""
    vi = Vi(NAME)
    flag = vi.control("flag", BOOL, REQUIRED)
    count = vi.indicator("count", I32)
    read = vi.local_variable(count)
    case = vi.case("boolean", BOOL, FRAMES, default_frame=None, tunnels=[(I32, False)])
    vi.wire(flag, case.selector)
    vi.wire(read, case.tunnels[0].outer)
    with vi.inside(case.scopes[1]):
        inc = increment(vi)
    vi.place_inside(count, case.scopes[1])
    vi.wire(case.tunnels[0].faces[1], inc["in"])
    vi.wire(inc["out"], count)
    project.add(vi)
    package = project.generate(NAME)
    calls = [True, True, False, True]
    assert [package.function(flag=f).count for f in calls] == [1, 2, 0, 1]


def test_a_preallocated_clone_notes_that_every_call_shares_the_value(
    project: Project,  # noqa: F811
) -> None:
    def preallocated(vi: Vi) -> None:
        vi.execution.update(IsReentrant="1", PooledReentrancy="0")

    _counter(project, preallocated)
    package = project.generate(NAME)
    lines = package.source.splitlines()
    kept = next(i for i, line in enumerate(lines) if line.startswith("_lv_indicator_"))
    assert NOTE in lines[kept - 1]
    assert [package.function().count for _ in range(2)] == [1, 2]


def test_a_shared_clone_has_no_note(project: Project) -> None:  # noqa: F811
    def shared(vi: Vi) -> None:
        vi.execution.update(IsReentrant="1", PooledReentrancy="1")

    _counter(project, shared)
    package = project.generate(NAME)
    assert NOTE not in package.source
    assert [package.function().count for _ in range(2)] == [1, 2]
