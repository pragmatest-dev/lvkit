"""The VI's toolbar (Run, Run Continuously, Abort, Pause) and the state of the run.

Run calls the VI's function in a worker thread (``run.io_bound``) and shows its
result when it returns. Run Continuously calls it again, latching the controls
each time, until its toggle is switched off or Abort is pressed. Abort makes the
next ``update`` or ``read`` of the holder raise ``Abort``; Pause makes them wait
until Resume. An ``error_out`` in the result is shown in a banner (an error is a
value; nothing is raised). The glyphs are our own shapes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from .live import Abort, LiveValues

_SVG_RUN = (
    '<svg width="15" height="15" viewBox="0 0 16 16">'
    '<polygon points="4,3 13,8 4,13" fill="currentColor"/></svg>'
)
_SVG_RUN_CONTINUOUSLY = (
    '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" '
    'stroke="currentColor" stroke-width="1.6">'
    '<path d="M12.5 8a4.5 4.5 0 1 1-1.3-3.2"/>'
    '<path d="M12.6 2.2 12.2 5l-2.7-.6" fill="currentColor" stroke="none"/></svg>'
)
_SVG_ABORT = (
    '<svg width="15" height="15" viewBox="0 0 16 16">'
    '<rect x="3.5" y="3.5" width="9" height="9" rx="1.5" fill="currentColor"/></svg>'
)
_SVG_PAUSE = (
    '<svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor">'
    '<rect x="4.5" y="3" width="2.6" height="10"/>'
    '<rect x="8.9" y="3" width="2.6" height="10"/></svg>'
)
_SVG_CLOSE = (
    '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" '
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round">'
    '<path d="M4 4l8 8M12 4l-8 8"/></svg>'
)
CONTINUOUS_PAUSE = 0.1

_BAR_CLS = (
    "w-full items-center gap-1 px-2 py-1 border-b bg-gray-50 "
    "dark:bg-neutral-800 dark:border-neutral-700"
)
_BANNER_CLS = (
    "w-full items-center gap-2 px-2 py-1 border-b bg-red-50 border-red-200 "
    "dark:bg-red-950 dark:border-red-900"
)


def error_text(error: Any) -> tuple[str, str]:
    """The headline (code and status) and the source of an error value."""
    kind = "error" if getattr(error, "status", True) else "warning"
    headline = f"Error {getattr(error, 'code', 0)} ({kind})"
    message = getattr(error, "message", "")
    if message:
        headline = f"{headline}: {message}"
    return headline, f"source: {getattr(error, 'source', '')}"


class RunController:
    """``call()`` runs the function (with the holder's hooks, if it has them) and
    returns its result; ``paint(result)`` shows the result on the indicators."""

    def __init__(
        self,
        live: LiveValues,
        call: Callable[[], Any],
        paint: Callable[[Any], None],
        *,
        abortable: bool,
    ) -> None:
        self._live = live
        self._call = call
        self._paint = paint
        self._abortable = abortable
        self._closed = False
        self.running = False
        self.continuous = False
        self._buttons: dict[str, ui.button] = {}
        self._status: ui.label | None = None
        self._banner: ui.row | None = None
        self._headline: ui.label | None = None
        self._source: ui.label | None = None

    # -- the state shown ---------------------------------------------------

    def _show_state(self, text: str) -> None:
        if self._status is not None:
            self._status.set_text(
                "closed" if self._closed else "paused" if self._live.paused else text
            )
        if not self._buttons or self._closed:
            return
        can_stop = self.running and (self._abortable or self.continuous)
        self._buttons["Run"].set_enabled(not self.running)
        self._buttons["Run Continuously"].set_enabled(
            not self.running or self.continuous
        )
        self._buttons["Run Continuously"].props(
            "color=green-9" if self.continuous else "color=green-7"
        )
        self._buttons["Abort"].set_enabled(can_stop)
        self._buttons["Pause"].set_enabled(can_stop)
        self._buttons["Pause"].props(
            "color=red-6" if self._live.paused else "color=grey-8"
        )

    def _show_error(self, result: Any) -> None:
        error = getattr(result, "error_out", None)
        if self._banner is None or self._headline is None or self._source is None:
            return
        if error is None:
            self._banner.set_visibility(False)
            return
        headline, source = error_text(error)
        self._headline.set_text(headline)
        self._source.set_text(source)
        self._banner.set_visibility(True)

    # -- the runs ----------------------------------------------------------

    async def _call_once(self) -> str:
        """One call of the function: how it ended."""
        events = self._live.panel_events
        if events is not None:
            events.start()
        try:
            result = await run.io_bound(self._call)
        except Abort:
            return "aborted"
        except Exception as exc:  # noqa: BLE001 -- Run is the boundary of the call
            return f"error: {type(exc).__name__}: {exc}"
        finally:
            if events is not None:
                events.stop()
        self._live.paint()
        self._paint(result)
        self._show_error(result)
        return "done"

    async def _session(self, *, continuous: bool) -> None:
        self._live.clear_abort()
        self._live.resume()
        self.running = True
        self.continuous = continuous
        if self._banner is not None:
            self._banner.set_visibility(False)
        self._show_state("running")
        while True:
            while self._live.paused:
                await asyncio.sleep(0.05)
            text = await self._call_once()
            if self._live.aborted:
                text = "aborted"
            if text != "done" or not self.continuous:
                break
            # A VI that returns at once would otherwise flood the browser with
            # updates faster than it takes them.
            await asyncio.sleep(CONTINUOUS_PAUSE)
            if not self.continuous:
                text = "aborted" if self._live.aborted else "done"
                break
        self.running = False
        self.continuous = False
        self._live.resume()
        self._show_state(text)

    async def run_once(self) -> None:
        if not self.running:
            await self._session(continuous=False)

    async def run_continuously(self) -> None:
        """Start Run Continuously; pressed again while it runs, stop after the
        current run."""
        if self.running:
            self.continuous = False
            self._show_state("running")
            return
        await self._session(continuous=True)

    async def close(self) -> None:
        """The panel's Close button. A running VI is asked first: its Panel
        Close? frame answers with Discard?, and True keeps the panel open. A
        browser tab closed by the browser's own control is not intercepted: the
        server cannot hold a tab open while the VI answers, so the VI keeps
        running until it ends or is aborted."""
        events = self._live.panel_events
        reply = events.ask_close() if events is not None else None
        if events is not None and reply is not None:
            answer = await run.io_bound(events.wait_for_reply, reply)
            if answer is not None and answer[0]:
                self._show_state("close discarded")
                return
        self._closed = True
        self._show_state("closed")
        for button in self._buttons.values():
            button.set_enabled(False)
        ui.run_javascript("window.close()")

    def abort(self) -> None:
        if self.running:
            self.continuous = False
            self._live.request_abort()
            self._show_state("aborting")

    def pause(self) -> None:
        """Pause, or resume when paused."""
        if not self.running:
            return
        if self._live.paused:
            self._live.resume()
        else:
            self._live.pause()
        self._show_state("running")

    # -- the widgets -------------------------------------------------------

    def toolbar(self) -> None:
        """Draw the buttons, the status and the error banner."""
        with ui.row().classes(_BAR_CLS):
            self._add("Run", _SVG_RUN, self.run_once, "green-7")
            self._add(
                "Run Continuously",
                _SVG_RUN_CONTINUOUSLY,
                self.run_continuously,
                "green-7",
            )
            self._add("Abort", _SVG_ABORT, self.abort, "red-6")
            self._add("Pause", _SVG_PAUSE, self.pause, "grey-8")
            if self._live.panel_events is not None:
                self._add("Close", _SVG_CLOSE, self.close, "grey-8")
            self._status = ui.label("idle").classes("text-xs text-gray-500")
        with ui.row().classes(_BANNER_CLS) as banner:
            ui.icon("error_outline").classes("text-red-600")
            self._headline = ui.label().classes("text-red-700 text-sm font-medium")
            self._source = ui.label().classes("text-red-700 text-xs")
        self._banner = banner
        banner.set_visibility(False)
        self._show_state("idle")

    def _add(
        self, tip: str, svg: str, on_click: Callable[[], Any], color: str
    ) -> None:
        button = ui.button(on_click=on_click, color=color).props("flat dense round")
        button.mark(tip.replace(" ", "-"))
        with button:
            ui.html(svg)
            ui.tooltip(tip)
        self._buttons[tip] = button
