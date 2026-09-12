"""VI toolbar / header -- reproduces a LabVIEW VI window's execution toolbar.
Clean-room glyphs (our own shapes; NEVER NI's noloc_env_*.gif artwork):
Run = solid triangle, Run Continuously = looping arrows, Abort = red stop
square (live only while running), Pause = two bars. Styleable via the active
Theme (see ``.theme``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from nicegui import ui

from ._util import _format_error
from .theme import theme

_SVG_RUN = (
    '<svg width="15" height="15" viewBox="0 0 16 16">'
    '<polygon points="4,3 13,8 4,13" fill="currentColor"/></svg>'
)
_SVG_RUNCONT = (
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


class RunController:
    """Drives a panel's execution the LabVIEW way, off ONE ``compute`` callable
    (async: latch the controls, call the pure logic, write the outputs):

    - **Run** runs ``compute`` once.
    - **Run Continuously** runs ``compute`` repeatedly until aborted/paused.
    - **Pause** suspends/resumes a continuous run.
    - **Abort** stops a run immediately.

    It is also the ERROR BOUNDARY -- the analog of LabVIEW's error-out merge.
    We strip error clusters in favour of natural Python exceptions, so the pure
    logic just raises; the controller catches that at the Run boundary, stores it
    (``error``/``error_detail``), stops the run, and the toolbar presents it as an
    error banner (see ``toolbar``). A fresh Run clears it.

    (A state-machine/event-structure VI will hand a longer-lived ``compute`` that
    loops internally; the same Abort stops it.) ``running``/``paused`` drive the
    toolbar's enabled states via ``_refresh``; ``error`` drives the banner via
    ``_on_error`` (both set by ``toolbar``)."""

    def __init__(self, compute: Callable[[], Any], *, interval: float = 0.1):
        self._compute = compute
        self._interval = interval
        self.running = False
        self.paused = False
        self.error: str | None = None
        self.error_detail: str | None = None
        self._task: asyncio.Task | None = None
        self._refresh: Callable[[], None] = lambda: None
        self._on_error: Callable[[], None] = lambda: None

    def _set_error(self, exc: BaseException) -> None:
        self.error, self.error_detail = _format_error(exc)
        self._on_error()

    def clear_error(self) -> None:
        if self.error is not None:
            self.error = None
            self.error_detail = None
            self._on_error()

    async def run_once(self) -> None:
        if self.running:
            return
        self.clear_error()
        self.running = True
        self._refresh()
        try:
            await self._compute()
        except Exception as exc:  # noqa: BLE001 -- Run is the error boundary
            self._set_error(exc)
        finally:
            self.running = False
            self._refresh()

    def run_continuous(self) -> None:
        # LabVIEW semantics: run the VI to COMPLETION, then run it again, until
        # aborted -- an async loop on the event loop (each iteration awaits the
        # whole compute; iterations never overlap), NOT a fixed-interval timer.
        if self.running:
            return
        self.clear_error()
        self.running = True
        self.paused = False
        self._refresh()
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        try:
            while self.running:
                if self.paused:
                    await asyncio.sleep(0.05)  # idle; Pause/Abort still land
                    continue
                await self._compute()  # run to completion before the next run
                await asyncio.sleep(self._interval)  # breather + yield the loop
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- Run is the error boundary
            self._set_error(exc)
        finally:
            self.running = False
            self.paused = False
            self._task = None
            self._refresh()

    def pause(self) -> None:
        if not self.running:
            return
        self.paused = not self.paused  # the loop reads this each iteration
        self._refresh()

    def abort(self) -> None:
        # Ask the loop to stop; it exits after the current run (its finally clears
        # running/paused and refreshes). Refresh now too so the toolbar responds
        # instantly rather than waiting out the last iteration.
        self.running = False
        self.paused = False
        self._refresh()


def _tb_button(svg: str, tip: str, on_click: Callable, *, enabled: bool,
               color: str) -> None:
    btn = (
        ui.button(on_click=on_click)
        .props("flat dense round")
        .classes(f"{color} min-w-0")
    )
    with btn:
        ui.html(svg)
        ui.tooltip(tip)
    if not enabled:
        btn.props("disable")


def toolbar(controller: RunController) -> None:
    """Render the VI execution toolbar bound to ``controller``. Run / Run
    Continuously are enabled when idle; Abort / Pause only while running. Below
    the buttons, an error banner shows any exception the last Run raised (our
    Python-exception stand-in for LabVIEW's error out) with an expandable
    traceback; it clears on the next Run."""

    @ui.refreshable
    def bar() -> None:
        running = controller.running
        with ui.row().classes(theme().toolbar_classes):
            _tb_button(_SVG_RUN, "Run", controller.run_once,
                       enabled=not running, color="text-green-700")
            _tb_button(_SVG_RUNCONT, "Run Continuously", controller.run_continuous,
                       enabled=not running, color="text-green-700")
            _tb_button(_SVG_ABORT, "Abort", controller.abort,
                       enabled=running, color="text-red-600")
            _tb_button(_SVG_PAUSE, "Pause", controller.pause,
                       enabled=running, color="text-gray-600")

    @ui.refreshable
    def error_banner() -> None:
        if not controller.error:
            return
        with ui.row().classes(
            "w-full items-start gap-2 px-2 py-1 border-b bg-red-50 "
            "border-red-200 dark:bg-red-950 dark:border-red-900"
        ):
            ui.icon("error_outline").classes("text-red-600 mt-0.5 shrink-0")
            with ui.column().classes("gap-0 grow min-w-0"):
                ui.label(controller.error).classes(
                    "text-red-700 dark:text-red-300 text-sm font-medium break-all"
                )
                if controller.error_detail:
                    with ui.expansion("traceback").classes("text-xs w-full"):
                        ui.code(controller.error_detail).classes(
                            "text-xs whitespace-pre-wrap w-full"
                        )
            ui.button(icon="close", on_click=controller.clear_error).props(
                "flat dense round size=xs"
            ).classes("text-red-500 shrink-0")

    controller._refresh = bar.refresh
    controller._on_error = error_banner.refresh
    bar()
    error_banner()
