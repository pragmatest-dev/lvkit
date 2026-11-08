"""A generated panel of a VI with an Event Structure: changing a control pushes a
Value Change to the VI while it waits, and the Close button asks the Panel Close?
frame (headless, with NiceGUI's user simulation)."""

from __future__ import annotations

import asyncio
import queue

import pytest
from nicegui import ui
from nicegui.testing.user import User

from lvkit.panel.runtime.events import PanelEvents
from lvkit.panel.runtime.live import Abort, LiveValues
from tests.error_diagrams import Project, project  # noqa: F401
from tests.event_diagrams import event_loop_vi
from tests.panel_harness import found, labels, panel_of, simulate, until

STEM = "events"


def _shown(user: User) -> list[str]:
    return [str(e.value) for e in found(user, ui.input)]


def _lights(user: User) -> list[bool]:
    """Whether each Boolean indicator (Timed Out, Closed) is lit."""
    return [
        "text-green-500" in e.classes
        for e in found(user, ui.icon)
        if e.props.get("name") == "circle"
    ]


async def _until_shown(user: User, value: str, seconds: float = 4.0) -> None:
    for _ in range(int(seconds / 0.05)):
        if value in _shown(user):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{value!r} not shown: {_shown(user)}")


def test_changing_a_control_while_the_vi_waits_runs_its_value_change_frame(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(timeout=1500))
    package = project.generate("Events.vi", panel=True)
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await until(user, "running")
        found(user, ui.number)[0].set_value(7)
        await _until_shown(user, "7")
        assert _shown(user)[:2] == ["7", "0"]  # Last New, Last Old
        found(user, ui.number)[0].set_value(9)
        await _until_shown(user, "9")
        assert _shown(user)[:2] == ["9", "7"]
        await until(user, "done")
        assert _lights(user) == [True, False]

    simulate(panel.build_panel, scenario)


def test_a_control_changed_while_the_vi_does_not_run_is_not_an_event(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(timeout=300))
    package = project.generate("Events.vi", panel=True)
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        found(user, ui.number)[0].set_value(5)
        user.find(marker="Run").click()
        await until(user, "done")
        assert _shown(user)[:2] == ["0", "0"]

    simulate(panel.build_panel, scenario)


def test_close_asks_the_panel_close_frame_and_closes_unless_it_discards(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(timeout=None))
    package = project.generate("Events.vi", panel=True)
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await until(user, "running")
        user.find(marker="Close").click()
        await until(user, "closed")
        await asyncio.sleep(0.3)
        assert "closed" in labels(user)
        assert _lights(user) == [False, True]

    simulate(panel.build_panel, scenario)


def test_close_stays_open_when_the_frame_discards(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(timeout=800, discard=True))
    package = project.generate("Events.vi", panel=True)
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await until(user, "running")
        user.find(marker="Close").click()
        await until(user, "close discarded")
        await until(user, "done")
        assert "closed" not in labels(user).replace("close discarded", "")

    simulate(panel.build_panel, scenario)


def test_abort_ends_a_vi_blocked_in_its_event_structure(
    project: Project,  # noqa: F811
) -> None:
    project.add(event_loop_vi(timeout=None))
    package = project.generate("Events.vi", panel=True)
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await until(user, "running")
        user.find(marker="Abort").click()
        await until(user, "aborted")

    simulate(panel.build_panel, scenario)


class _Kinds:
    VALUE_CHANGE = "Value Change"
    PANEL_CLOSE = "Panel Close?"


def test_events_are_queued_only_while_listening() -> None:
    live = LiveValues(_Kinds)
    assert live.events is not None
    live.add_control("gain", 1)
    live.set_control("gain", 2)
    assert live.events.queue.empty()
    live.events.start()
    live.set_control("gain", 3)
    assert live.events.queue.get() == ("gain", 3, "Value Change", 2)
    live.events.stop()
    assert live.events.ask_close() is None


def test_a_wait_for_an_event_keeps_its_timeout_and_ends_on_abort() -> None:
    live = LiveValues(_Kinds)
    assert live.events is not None
    inbox = live.events.queue
    with pytest.raises(queue.Empty):
        inbox.get(timeout=0.05)
    live.request_abort()
    with pytest.raises(Abort):
        inbox.get(timeout=5)


def test_a_vi_without_an_event_structure_has_no_events() -> None:
    assert LiveValues().events is None
    assert isinstance(LiveValues(_Kinds).events, PanelEvents)
