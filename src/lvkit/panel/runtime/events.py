"""The front panel's events, pushed to the Event Structure of a running VI.

A message is ``(name, new_value, kind, old_value)``; a Panel Close? message has a
fifth item, the reply queue its frame answers on with ``(discard, value)``. The
kinds are the members of the event enum the VI's module defines. Events are only
queued while the VI runs: a VI that is not running has no Event Structure waiting.
The queue belongs to the run of the panel (``Panel.events``); this view pushes
into it.
"""

from __future__ import annotations

import copy
from queue import Empty, Queue
from typing import Any

REPLY_POLL = 0.2


class PanelEvents:
    """The user's events for the Event Structure of the VI that runs on ``panel``.
    The queue is the run's (``Panel.events``); a run that takes no events, or no
    run at all, has none, so nothing is queued then."""

    def __init__(self, panel: Any, kinds: Any) -> None:
        self._panel = panel
        self._kinds = kinds
        self._asked: Queue | None = None

    def value_changed(self, name: str, value: Any, old: Any) -> None:
        """The user changed the control ``name``."""
        queue = self._panel.events
        if queue is not None:
            queue.put((name, copy.deepcopy(value), self._kinds.VALUE_CHANGE, old))

    def ask_close(self) -> Queue | None:
        """Ask the VI to close its panel: the queue its answer arrives on, None
        when the VI is not running (nothing to ask)."""
        queue = self._panel.events
        if queue is None:
            return None
        self._asked = queue
        reply: Queue = Queue(maxsize=1)
        queue.put(("", None, self._kinds.PANEL_CLOSE, None, reply))
        return reply

    def wait_for_reply(self, reply: Queue) -> tuple[bool, Any] | None:
        """The ``(discard, value)`` the VI answered; None when the run ended
        without an answer. Blocks, so it runs in a thread."""
        while True:
            try:
                return reply.get(timeout=REPLY_POLL)
            except Empty:
                if self._panel.events is not self._asked:
                    return None
