"""A generated panel of a VI with an Event Structure: changing a control pushes a
Value Change to the VI while it waits, and the Close button asks the Panel Close?
frame (headless, with NiceGUI's user simulation)."""

from __future__ import annotations

import asyncio
import time
from queue import Queue
from types import SimpleNamespace

from nicegui import ui
from nicegui.testing.user import User

from lvkit.panel.runtime.events import PanelEvents
from lvkit.panel.runtime.view import PanelWidgets
from tests.error_diagrams import Project, project  # noqa: F401
from tests.event_diagrams import event_loop_vi
from tests.helpers import generated_panels
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


def test_events_are_queued_only_while_the_panel_has_a_run_queue() -> None:
    panel = SimpleNamespace(events=None)
    events = PanelEvents(panel, _Kinds)
    events.value_changed("gain", 2, 1)
    assert events.ask_close() is None
    panel.events = Queue()
    events.value_changed("gain", 3, 2)
    assert panel.events.get() == ("gain", 3, "Value Change", 2)
    reply = events.ask_close()
    assert reply is not None
    assert panel.events.get() == ("", None, "Panel Close?", None, reply)
    panel.events = None
    assert events.wait_for_reply(reply) is None


def test_a_wait_for_an_event_keeps_its_timeout_and_ends_on_abort() -> None:
    panels = generated_panels()
    waited: list[str] = []

    def waits(live_io: object = None) -> None:
        inbox = live_io.events  # type: ignore[attr-defined]
        try:
            inbox.get(timeout=0.05)
        except Exception as raised:  # noqa: BLE001 -- the timeout is the case
            waited.append(type(raised).__name__)
        inbox.get(timeout=5)

    panel = panels.Panel(waits)
    panel.run(wait=False)
    started = time.monotonic()
    while not waited and time.monotonic() - started < 3:
        time.sleep(0.01)
    assert waited == ["Empty"]
    panel.abort()
    while panel.state != 1 and time.monotonic() - started < 3:
        time.sleep(0.01)
    assert panel.state == 1
    assert panel.aborted


def test_a_vi_without_an_event_structure_has_no_events() -> None:
    panel = SimpleNamespace(events=None)
    assert PanelWidgets().panel_events is None
    assert PanelWidgets(panel).panel_events is None
    assert isinstance(PanelWidgets(panel, None, _Kinds).panel_events, PanelEvents)
