"""The live values of a running VI, shared by the worker thread that runs it and
the NiceGUI event loop that shows it.

Only NiceGUI's loop touches a widget. The running function calls ``update`` and
``read`` from its worker thread; they touch plain data under a lock, never a
widget. One timer on the loop calls ``paint``, which writes the latest value of
each changed indicator into its widget.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from typing import Any


class Abort(BaseException):
    """Raised from ``update`` and ``read`` once Abort was pressed, so the running
    function ends at its next panel value. A ``BaseException`` so no
    ``except Exception`` in the function swallows it."""


def key_of(name: Any) -> str:
    """The string a hook name stands for: the value of the enum member the
    function passes, or the string itself."""
    return str(getattr(name, "value", name))


class LiveValues:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] = {}
        self._dirty: dict[str, None] = {}
        self._controls: dict[str, Any] = {}
        self._painters: dict[str, Callable[[Any], None]] = {}
        self._abort = False
        self._running = threading.Event()
        self._running.set()

    # -- the widgets (on the event loop) -----------------------------------

    def add_control(self, name: Any, value: Any) -> None:
        """A control with its starting value."""
        self._controls[key_of(name)] = value

    def set_control(self, name: Any, value: Any) -> None:
        """A control the user changed (called from its change handler)."""
        self._controls[key_of(name)] = value

    def add_indicator(self, name: Any, painter: Callable[[Any], None]) -> None:
        """An indicator, with the function that shows a value in its widget."""
        self._painters[key_of(name)] = painter

    def value(self, name: Any) -> Any:
        """The current value of a control."""
        return self._controls[key_of(name)]

    def show(self, name: Any, value: Any) -> None:
        """Show ``value`` in an indicator now (on the event loop)."""
        self._painters[key_of(name)](value)

    def paint(self) -> None:
        """Show the latest value of every indicator that changed since the last
        call. Called by the timer, on the event loop."""
        with self._lock:
            changed = [(k, self._latest[k]) for k in self._dirty]
            self._dirty.clear()
        for key, value in changed:
            painter = self._painters.get(key)
            if painter is not None:
                painter(value)

    # -- the abort flag ------------------------------------------------------

    def request_abort(self) -> None:
        """Abort the run at its next hook call; a paused run is released so it
        reaches that call."""
        self._abort = True
        self._running.set()

    def clear_abort(self) -> None:
        self._abort = False

    @property
    def aborted(self) -> bool:
        return self._abort

    # -- pause ---------------------------------------------------------------

    @property
    def paused(self) -> bool:
        return not self._running.is_set()

    def pause(self) -> None:
        """Hold the running function at its next hook call."""
        self._running.clear()

    def resume(self) -> None:
        """Release the function held by ``pause``."""
        self._running.set()

    def _checkpoint(self) -> None:
        """Every hook call: block while paused, then end the run if it was
        aborted."""
        self._running.wait()
        if self._abort:
            raise Abort

    # -- the hooks (on the worker thread) ---------------------------------

    def update(self, name: Any, value: Any) -> None:
        """``on_update``: the function wrote an indicator."""
        self._checkpoint()
        key = key_of(name)
        snapshot = copy.copy(value)
        with self._lock:
            self._latest[key] = snapshot
            self._dirty[key] = None

    def read(self, name: Any, value: Any) -> Any:
        """``on_read``: the control's current value, or ``value`` (the one the
        function holds) when the panel has no such control."""
        self._checkpoint()
        return self._controls.get(key_of(name), value)
