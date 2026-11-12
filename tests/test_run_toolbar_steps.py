"""The toolbar's state follows the panel's run notices, which reach it later than
its own Run click (``PanelWidgets.paint`` passes them on from a timer). A late
"started" notice of a run the toolbar began itself must not replace what the
toolbar showed since, such as "close discarded" (issue #297). Each case calls
the toolbar in the order the notices can arrive, with no timer and no browser."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from lvkit.panel.runtime import runtoolbar
from lvkit.panel.runtime.runtoolbar import RunController

Listener = Callable[[str, BaseException | None], None]


class _Panel:
    """A panel whose run is going until ``end`` is called."""

    paused = False
    aborted = False

    def __init__(self) -> None:
        self.state = 1  # idle

    def run(self, wait: bool = True) -> None:
        self.state = 2

    def end(self) -> None:
        self.state = 1

    def holds(self, name: str) -> bool:
        return False


class _Events:
    """The VI's Panel Close? frame, answering Discard? = True."""

    def ask_close(self) -> object:
        return object()

    def wait_for_reply(self, reply: object) -> tuple[bool, None]:
        return (True, None)


class _View:
    def __init__(self, panel: _Panel, events: _Events | None = None) -> None:
        self.panel = panel
        self.panel_events = events
        self.listener: Listener | None = None

    def on_run(self, listener: Listener) -> None:
        self.listener = listener

    def notice(self, kind: str) -> None:
        """The panel's notice, as ``PanelWidgets.paint`` passes it on."""
        assert self.listener is not None
        self.listener(kind, None)


@pytest.fixture(autouse=True)
def io_on_this_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    async def io_bound(function: Callable[..., Any], *args: Any) -> Any:
        return function(*args)

    monkeypatch.setattr(runtoolbar.run, "io_bound", io_bound)


def _toolbar(events: _Events | None = None) -> tuple[RunController, _View, _Panel]:
    panel = _Panel()
    view = _View(panel, events)
    return RunController(view, abortable=True), view, panel  # type: ignore[arg-type]


def test_a_late_started_notice_keeps_close_discarded() -> None:
    toolbar, view, panel = _toolbar(_Events())
    toolbar.run_once()
    asyncio.run(toolbar.close())
    assert toolbar._status_text == "close discarded"
    view.notice("started")
    assert toolbar._status_text == "close discarded"
    assert toolbar.running
    panel.end()
    view.notice("finished")
    assert toolbar._status_text == "done"
    assert not toolbar.running


def test_a_run_the_toolbar_did_not_start_shows_running() -> None:
    toolbar, view, panel = _toolbar()
    panel.run(wait=False)  # started by reference, not from the toolbar
    view.notice("started")
    assert toolbar._status_text == "running"
    assert toolbar.running


def test_run_continuously_shows_running_between_its_runs() -> None:
    toolbar, view, panel = _toolbar()
    toolbar.run_continuously()
    view.notice("started")
    assert toolbar._status_text == "running"
    panel.end()
    toolbar.continuous = False  # switched off during the run
    view.notice("finished")
    assert toolbar._status_text == "done"
