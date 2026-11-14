"""A Property Node bound to a control whose row is ``Value (Signaling)`` writes the
control the way ``Value`` does and, when an Event Structure of the VI has a Value
Change frame for the control, puts the event on the VI's queue, as the live panel
does when the user edits the control (``codegen/nodes/property_node.py``,
``codegen/nodes/event_user.py``). Each case is a tiny diagram, generated and run."""

from __future__ import annotations

from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.event_diagrams import (
    _I32,
    event_structure,
    timeout_frame,
    value_change_frame,
)
from tests.live_io_recorder import Recorder
from tests.panel_diagrams import value_property
from tests.panel_harness import panel_of

NAME = "Sig.vi"
SIGNALING = "Value (Signaling)"


def _signaling_vi(
    *,
    handles_change: bool = True,
    write: str = SIGNALING,
    indicator: bool = False,
    value: int = 5,
) -> Vi:
    """A control ``N``, a While Loop with an Event Structure whose Value Change
    frame for ``N`` shows NewVal in ``New`` and OldVal in ``Old`` and ends the loop
    (its Timeout frame sets ``Timed Out`` and ends it too), and a Property Node
    writing ``value`` to the ``write`` row of ``N`` before the loop. Without
    ``handles_change`` the structure has the Timeout frame only. With ``indicator``
    the node is bound to the indicator ``Mirror`` instead."""
    vi = Vi(NAME)
    n = vi.control("N", I32, REQUIRED, bounds=(40, 20, 62, 160))
    mirror = vi.indicator("Mirror", I32, bounds=(40, 400, 62, 540))
    new = vi.indicator("New", I32, bounds=(40, 200, 62, 340))
    old = vi.indicator("Old", I32, bounds=(80, 200, 102, 340))
    timed_out = vi.indicator(
        "Timed Out", BOOL, ddo_class="stdBool", bounds=(120, 200, 150, 320)
    )
    (row,) = value_property(vi, mirror if indicator else n, [write])
    vi.wire(vi.int_constant(value), row)
    loop = vi.loop("whileLoop")
    with vi.inside(loop.scopes[0]):
        frames = [timeout_frame()]
        if handles_change:
            frames.append(value_change_frame([mirror if indicator else n], _I32))
        events = event_structure(vi, frames, outputs=[BOOL])
        wait = vi.int_constant(300)
    stop = events.outputs[0]
    vi.wire(wait, events.timeout)
    vi.wire(stop.outer, loop.faces[0])
    timeout_terms = events.frames[0]
    vi.place_inside(timed_out, timeout_terms.scope)
    with vi.inside(timeout_terms.scope):
        vi.wire(vi.bool_constant(True), timed_out)
        vi.wire(vi.bool_constant(True), stop.faces[0])
    if handles_change:
        change_terms = events.frames[1]
        for pane in (new, old):
            vi.place_inside(pane, change_terms.scope)
        vi.wire(change_terms.data["NewVal"], new)
        vi.wire(change_terms.data["OldVal"], old)
        with vi.inside(change_terms.scope):
            vi.wire(vi.bool_constant(True), stop.faces[1])
    return vi


def _generate(project: Project, vi: Vi) -> Package:  # noqa: F811
    project.add(vi)
    return project.generate(NAME)


def test_a_signaled_write_runs_the_value_change_frame_with_the_old_value(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project, _signaling_vi())
    assert package.gaps == []
    result = package.function(n=3)
    assert (result.new, result.old) == (5, 3)
    assert result.timed_out is False


def test_the_event_is_the_message_the_panel_puts_for_a_user_edit(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project, _signaling_vi())
    assert "old_value = n\n" in package.source
    assert "n = 5\n" in package.source
    assert (
        "events.put((SigValue.N, copy.deepcopy(n), SigEvent.VALUE_CHANGE, old_value))"
        in package.source
    )
    assert "live_io.events" in package.source


def test_a_signaled_write_of_the_value_the_control_holds_still_fires(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project, _signaling_vi(value=5))
    result = package.function(n=5)
    assert (result.new, result.old) == (5, 5)
    assert result.timed_out is False


def test_a_plain_value_write_fires_no_event(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project, _signaling_vi(write="Value"))
    assert package.gaps == []
    assert "events.put" not in package.source
    assert package.function(n=3).timed_out is True


def test_a_signaled_write_without_a_frame_for_the_control_is_the_plain_write(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project, _signaling_vi(handles_change=False))
    assert package.gaps == []
    assert "events.put" not in package.source
    assert "old_value" not in package.source
    assert "copy." not in package.source
    assert "n = 5\n" in package.source
    assert package.function(n=3).timed_out is True


def test_a_signaled_write_in_a_vi_with_no_event_structure_is_the_plain_write(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("N", I32, REQUIRED)
    out = vi.indicator("Out", I32)
    (row,) = value_property(vi, n, [SIGNALING])
    vi.wire(vi.int_constant(5), row)
    vi.wire(vi.int_constant(0), out)
    package = _generate(project, vi)
    assert package.gaps == []
    assert "Queue" not in package.source
    assert "events" not in package.source
    assert "n = 5\n" in package.source
    recorder = Recorder()
    package.function(n=1, live_io=recorder)
    assert any(value == 5 for _, value in recorder.calls)


def test_a_signaled_write_reaches_the_queue_of_a_run_through_a_panel(
    project: Project,  # noqa: F811
) -> None:
    project.add(_signaling_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, "sig").vi_function.panel
    panel.set("N", 3)
    panel.run()
    assert panel.get("New") == 5
    assert panel.get("Old") == 3
    assert panel.get("Timed Out") is False


def test_a_signaled_write_of_an_indicator_fires_its_value_change_frame(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project, _signaling_vi(indicator=True))
    assert package.gaps == []
    result = package.function(n=3)
    assert (result.new, result.old) == (5, 0)
    assert result.mirror == 5
    assert result.timed_out is False
    assert "old_value = mirror\n" in package.source
    assert "events.put((SigValue.MIRROR, copy.deepcopy(mirror)" in package.source


def test_a_signaled_write_of_an_indicator_without_a_frame_is_the_plain_write(
    project: Project,  # noqa: F811
) -> None:
    package = _generate(project, _signaling_vi(indicator=True, handles_change=False))
    assert package.gaps == []
    assert "events.put" not in package.source
    assert "old_value" not in package.source
    assert package.function(n=3).mirror == 5


def test_a_signaled_read_stays_a_gap(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    n = vi.control("N", I32, REQUIRED)
    out = vi.indicator("Out", I32)
    (row,) = value_property(vi, n, [SIGNALING])
    vi.wire(row, out)
    package = _generate(project, vi)
    assert len(package.gaps) == 1
