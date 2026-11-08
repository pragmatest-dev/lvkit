"""The generated function of a VI with an Event Structure, run with a prefilled
``queue.Queue`` of the panel's events."""

from __future__ import annotations

import inspect
import queue
from typing import Any

import pytest

from lvkit.codegen.gaps import UNSUPPORTED_NODE
from tests.error_diagrams import Package, Project, project  # noqa: F401
from tests.event_diagrams import event_loop_vi, mouse_down_frame
from tests.panel_diagrams import NAME, counting_vi


def _function(package: Package) -> Any:
    return package.module.events_


def _run(package: Package, *messages: tuple[Any, ...], **keywords: Any) -> Any:
    """Call the function with the messages in a queue, and the writes of its
    indicators in ``updates``."""
    events: queue.Queue = queue.Queue()
    for message in messages:
        events.put(message)
    updates: list[tuple[str, Any]] = []
    result = _function(package)(
        0,
        0,
        on_update=lambda name, value: updates.append((name.name, value)),
        events=events,
        **keywords,
    )
    return result, updates


def _kinds(package: Package) -> Any:
    return package.module.EventsEvent


def _names(package: Package) -> Any:
    return package.module.EventsValue


def test_a_value_change_then_a_timeout_run_their_frames_in_order(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi())
    package = project.generate("Events.vi")
    names, kinds = _names(package), _kinds(package)
    result, updates = _run(
        package,
        (names.GAIN, 7, kinds.VALUE_CHANGE, 3),
        (names.OFFSET, 9, kinds.VALUE_CHANGE, 8),
    )
    assert updates == [
        ("LAST_OLD", 3),
        ("LAST_NEW", 7),
        ("LAST_OLD", 8),
        ("LAST_NEW", 9),
        ("TIMED_OUT", True),
    ]
    assert (result.last_new, result.last_old) == (9, 8)
    assert (result.timed_out, result.closed) == (True, False)
    assert package.gaps == []


def test_a_message_is_matched_by_its_control_and_kind_not_by_a_string_of_its_own(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi())
    package = project.generate("Events.vi")
    kinds = _kinds(package)
    result, updates = _run(
        package,
        ("gain", 4, "Value Change", 1),
        ("unknown", 5, kinds.VALUE_CHANGE, 2),
    )
    assert updates[:2] == [("LAST_OLD", 1), ("LAST_NEW", 4)]
    assert result.last_new == 4


@pytest.mark.parametrize("discard", [False, True])
def test_panel_close_answers_with_the_discard_of_its_frame(
    project: Project,  # noqa: F811
    discard: bool,
) -> None:
    project.add(event_loop_vi(discard=discard))
    package = project.generate("Events.vi")
    reply: queue.Queue = queue.Queue(maxsize=1)
    result, updates = _run(
        package, ("", None, _kinds(package).PANEL_CLOSE, None, reply)
    )
    assert reply.get_nowait() == (discard, None)
    # a frame that discards the close leaves the VI running: it ends on its Timeout
    assert updates == [("CLOSED", True)] + ([("TIMED_OUT", True)] if discard else [])
    assert (result.closed, result.timed_out) == (True, discard)


def test_a_filter_event_no_frame_handles_is_not_discarded(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi())
    package = project.generate("Events.vi")
    reply: queue.Queue = queue.Queue(maxsize=1)
    result, updates = _run(package, ("", None, "Some Filter?", None, reply))
    assert reply.get_nowait() == (False, None)
    assert updates == [("TIMED_OUT", True)]
    assert result.timed_out is True


def test_no_events_and_a_timeout_runs_the_timeout_frame_after_waiting(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(timeout=10))
    package = project.generate("Events.vi")
    result = _function(package)(0, 0)
    assert result.timed_out is True


def test_no_events_and_no_timeout_is_a_gap_that_raises_where_it_waits(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(timeout=None))
    package = project.generate("Events.vi")
    assert len(package.gaps) == 1
    gap = package.gaps[0]
    assert gap.kind == UNSUPPORTED_NODE
    assert "pass events=" in gap.reason
    with pytest.raises(NotImplementedError, match="pass events="):
        _function(package)(0, 0)
    # with the events given, the same function runs
    events: queue.Queue = queue.Queue()
    events.put((_names(package).GAIN, 2, _kinds(package).VALUE_CHANGE, 1))
    reply: queue.Queue = queue.Queue(maxsize=1)
    events.put(("", None, _kinds(package).PANEL_CLOSE, None, reply))
    result = _function(package)(0, 0, events=events)
    assert (result.last_new, result.closed) == (2, True)


def test_the_frame_sees_the_new_value_of_the_control_its_event_changed(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(read_gain=True))
    package = project.generate("Events.vi")
    names, kinds = _names(package), _kinds(package)
    stale = {names.GAIN: 3, names.OFFSET: 30}
    result, _ = _run(
        package,
        (names.GAIN, 7, kinds.VALUE_CHANGE, 3),
        on_read=lambda name, value: stale.get(name, value),
    )
    assert result.seen_gain == 7
    # a change of the other control leaves the Gain terminal as the panel holds it
    result, _ = _run(
        package,
        (names.OFFSET, 31, kinds.VALUE_CHANGE, 30),
        on_read=lambda name, value: stale.get(name, value),
    )
    assert result.seen_gain == 3


def test_an_event_lvkit_does_not_handle_is_a_gap_that_names_it(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(extra=mouse_down_frame()))
    package = project.generate("Events.vi")
    assert len(package.gaps) == 1
    assert "Mouse Down" in package.gaps[0].reason
    with pytest.raises(NotImplementedError, match="Mouse Down"):
        _function(package)(0, 0, events=queue.Queue())


def test_a_vi_without_an_event_structure_takes_no_events(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME)
    assert "events" not in inspect.signature(package.function).parameters
    assert "Queue" not in package.source
