"""The VI's toolbar (Run, Run Continuously, Abort, Pause) and the state of the run.

The toolbar drives the ``Panel`` of the VI's function, the same path as a Run VI
by reference: Run is ``panel.run(wait=False)``, Abort, Pause and Resume are the
panel's. What it shows follows the panel's own notices (a run began, a run ended),
so a run another VI started shows here too. Run Continuously starts the next run
when one ends, until its toggle is switched off or Abort is pressed. A Run while
the VI runs is refused by the panel (error 1000) and the refusal is shown. An
error the run raised, or an ``error_out`` indicator holding one, is shown in a
banner (an error is a value; nothing is raised). The glyphs are our own shapes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from .view import PanelWidgets

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
    """The toolbar of ``view.panel``. ``error_label`` is the label of the
    indicator that holds the VI's ``error_out`` (None when it has none)."""

    def __init__(
        self,
        view: PanelWidgets,
        *,
        abortable: bool,
        error_label: str | None = None,
    ) -> None:
        self._view = view
        self._panel = view.panel
        self._error_label = error_label
        self._abortable = abortable
        self._closed = False
        self.running = self._panel.state != 1
        self.continuous = False
        self._status_text = "idle"
        self._buttons: dict[str, ui.button] = {}
        self._status: ui.label | None = None
        self._banner: ui.row | None = None
        self._headline: ui.label | None = None
        self._source: ui.label | None = None
        view.on_run(self._on_run)

    # -- the state shown ---------------------------------------------------

    def _show_state(self, text: str) -> None:
        self._status_text = text
        if self._status is not None:
            self._status.set_text(
                "closed" if self._closed else "paused" if self._panel.paused else text
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
            "color=red-6" if self._panel.paused else "color=grey-8"
        )

    def _show_error(self, error: Any) -> None:
        """The banner for ``error``: a LabVIEW error value, or None to hide it."""
        if self._banner is None or self._headline is None or self._source is None:
            return
        if error is None:
            self._banner.set_visibility(False)
            return
        headline, source = error_text(error)
        self._headline.set_text(headline)
        self._source.set_text(source)
        self._banner.set_visibility(True)

    def _error_out(self) -> Any:
        """The error the VI left in its ``error_out`` indicator."""
        if self._error_label is None or not self._panel.has(self._error_label):
            return None
        return self._panel.get(self._error_label)

    # -- the runs ----------------------------------------------------------

    def _on_run(self, kind: str, error: BaseException | None) -> None:
        """The panel's run began or ended (``PanelWidgets.paint``, on the loop)."""
        if kind == "started":
            self.running = True
            self._show_state("running")
            return
        self.running = self._panel.state != 1
        if self.running:  # the next run has already begun
            return
        self._show_error(None)
        if self._panel.aborted:
            text = "aborted"
        elif error is not None:
            text = f"error: {type(error).__name__}: {error}"
        else:
            text = "done"
            self._show_error(self._error_out())
        if text == "done" and self.continuous:
            # A VI that returns at once would otherwise flood the browser with
            # updates faster than it takes them.
            asyncio.get_running_loop().call_later(CONTINUOUS_PAUSE, self._again)
            self._show_state("running")
            return
        self.continuous = False
        self._show_state(text)

    def _again(self) -> None:
        if self.continuous and not self.running:
            self._start()

    def _start(self) -> None:
        """Run the VI through its panel; a refused run is shown."""
        try:
            self._panel.run(wait=False)
        except Exception as refused:  # noqa: BLE001 -- the panel's error is shown
            self.continuous = False
            self._show_error(refused)
            self._show_state(self._status_text)
            return
        self.running = True
        self._show_error(None)
        self._show_state("running")

    def run_once(self) -> None:
        self._start()

    def run_continuously(self) -> None:
        """Start Run Continuously; pressed again while it runs, stop after the
        current run."""
        if self.running:
            self.continuous = False
            self._show_state("running")
            return
        self.continuous = True
        self._start()

    async def close(self) -> None:
        """The panel's Close button. A running VI is asked first: its Panel
        Close? frame answers with Discard?, and True keeps the panel open. A
        browser tab closed by the browser's own control is not intercepted: the
        server cannot hold a tab open while the VI answers, so the VI keeps
        running until it ends or is aborted."""
        events = self._view.panel_events
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
            self._panel.abort()
            self._show_state("aborting")

    def pause(self) -> None:
        """Pause, or resume when paused."""
        if not self.running:
            return
        if self._panel.paused:
            self._panel.resume()
        else:
            self._panel.pause()
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
            if self._view.panel_events is not None:
                self._add("Close", _SVG_CLOSE, self.close, "grey-8")
            self._status = ui.label("idle").classes("text-xs text-gray-500")
        with ui.row().classes(_BANNER_CLS) as banner:
            ui.icon("error_outline").classes("text-red-600")
            self._headline = ui.label().classes("text-red-700 text-sm font-medium")
            self._source = ui.label().classes("text-red-700 text-xs")
        self._banner = banner
        banner.set_visibility(False)
        self._show_state("running" if self.running else "idle")

    def _add(self, tip: str, svg: str, on_click: Callable[[], Any], color: str) -> None:
        button = ui.button(on_click=on_click, color=color).props("flat dense round")
        button.mark(tip.replace(" ", "-"))
        with button:
            ui.html(svg)
            ui.tooltip(tip)
        self._buttons[tip] = button
