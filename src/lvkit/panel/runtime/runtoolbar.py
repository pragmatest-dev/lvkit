"""The VI's Run and Abort buttons and the state of the run.

Run calls the VI's function in a worker thread (``run.io_bound``) and shows its
result when it returns; it is disabled while the function runs. Abort makes the
next ``update`` or ``read`` of the holder raise ``Abort``, which ends the run.
The glyphs are our own shapes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import run, ui

from .live import Abort, LiveValues

_SVG_RUN = (
    '<svg width="15" height="15" viewBox="0 0 16 16">'
    '<polygon points="4,3 13,8 4,13" fill="currentColor"/></svg>'
)
_SVG_ABORT = (
    '<svg width="15" height="15" viewBox="0 0 16 16">'
    '<rect x="3.5" y="3.5" width="9" height="9" rx="1.5" fill="currentColor"/></svg>'
)
_BAR_CLS = (
    "w-full items-center gap-1 px-2 py-1 border-b bg-gray-50 "
    "dark:bg-neutral-800 dark:border-neutral-700"
)


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
        self.running = False
        self._run_button: ui.button | None = None
        self._abort_button: ui.button | None = None
        self._status: ui.label | None = None

    def _show_state(self, text: str) -> None:
        if self._status is not None:
            self._status.set_text(text)
        if self._run_button is not None:
            self._run_button.set_enabled(not self.running)
        if self._abort_button is not None:
            self._abort_button.set_enabled(self.running and self._abortable)

    async def run_once(self) -> None:
        if self.running:
            return
        self._live.clear_abort()
        self.running = True
        self._show_state("running")
        text = "done"
        try:
            result = await run.io_bound(self._call)
        except Abort:
            text = "aborted"
        except Exception as exc:  # noqa: BLE001 -- Run is the boundary of the call
            text = f"error: {type(exc).__name__}: {exc}"
        else:
            self._live.paint()
            self._paint(result)
        self.running = False
        self._show_state(text)

    def abort(self) -> None:
        if self.running:
            self._live.request_abort()

    def toolbar(self) -> None:
        """Draw the buttons and the status."""
        with ui.row().classes(_BAR_CLS):
            self._run_button = self._button(_SVG_RUN, "Run", self.run_once, "green-7")
            self._abort_button = self._button(_SVG_ABORT, "Abort", self.abort, "red-6")
            self._abort_button.set_enabled(False)
            self._status = ui.label("idle").classes("text-xs text-gray-500")

    @staticmethod
    def _button(
        svg: str, tip: str, on_click: Callable[[], Any], color: str
    ) -> ui.button:
        button = ui.button(on_click=on_click, color=color).props("flat dense round")
        button.mark(tip)
        with button:
            ui.html(svg)
            ui.tooltip(tip)
        return button
