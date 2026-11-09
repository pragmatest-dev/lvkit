"""The front panel's events, pushed to the Event Structure of a running VI.

A message is ``(name, new_value, kind, old_value)``; a Panel Close? message has a
fifth item, the reply queue its frame answers on with ``(discard, value)``. The
kinds are the members of the event enum the VI's module defines. Events are only
queued while the VI runs: a VI that is not running has no Event Structure waiting.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable
from queue import Empty, Queue
from typing import Any

REPLY_POLL = 0.2
ABORT_POLL = 0.1


class _Inbox(Queue):
    """The queue the VI reads its events from. A wait for the next event also
    ends the run when Abort was pressed and holds while the run is paused (the
    way a panel value the VI reads or writes does), so a VI blocked in its Event
    Structure can be aborted; the wait's own timeout is kept."""

    def __init__(self, checkpoint: Callable[[], None]) -> None:
        super().__init__()
        self._checkpoint = checkpoint

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        if not block:
            return super().get(block=False)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._checkpoint()
            step = ABORT_POLL
            if deadline is not None:
                step = min(step, max(0.0, deadline - time.monotonic()))
            try:
                return super().get(timeout=step)
            except Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    raise


class PanelEvents:
    def __init__(self, kinds: Any, checkpoint: Callable[[], None]) -> None:
        self.queue: Queue = _Inbox(checkpoint)
        self._kinds = kinds
        self._listening = False

    def start(self) -> None:
        """A run begins: forget the events of the last one and queue new ones."""
        while True:
            try:
                self.queue.get_nowait()
            except Empty:
                break
        self._listening = True

    def stop(self) -> None:
        """The run ended."""
        self._listening = False

    @property
    def listening(self) -> bool:
        return self._listening

    def value_changed(self, name: str, value: Any, old: Any) -> None:
        """The user changed the control ``name``."""
        if self._listening:
            self.queue.put(
                (name, copy.deepcopy(value), self._kinds.VALUE_CHANGE, old)
            )

    def ask_close(self) -> Queue | None:
        """Ask the VI to close its panel: the queue its answer arrives on, None
        when the VI is not running (nothing to ask)."""
        if not self._listening:
            return None
        reply: Queue = Queue(maxsize=1)
        self.queue.put(("", None, self._kinds.PANEL_CLOSE, None, reply))
        return reply

    def wait_for_reply(self, reply: Queue) -> tuple[bool, Any] | None:
        """The ``(discard, value)`` the VI answered; None when the run ended
        without an answer. Blocks, so it runs in a thread."""
        while True:
            try:
                return reply.get(timeout=REPLY_POLL)
            except Empty:
                if not self._listening:
                    return None
