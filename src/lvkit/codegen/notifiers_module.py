"""The module a generated package holds for LabVIEW notifiers.

A notifier keeps only the latest message, wakes every function waiting on it
when a message is sent, and lets each Wait on Notification node wait for the
next message it has not yet received. ``queue.Queue`` and ``threading.Event``
do none of the three, so the package gets a small ``Notifier`` class on
``threading.Condition``. It is written once into the package root as
``notifiers.py``, only when a generated module imports it, and imported from
there, so a generated package imports nothing from lvkit.
"""

from __future__ import annotations

NOTIFIERS_MODULE_NAME = "notifiers"
NOTIFIER_NAME = "Notifier"

NOTIFIERS_MODULE_SOURCE = '''"""LabVIEW notifiers."""

from __future__ import annotations

import itertools
import threading
import time
from typing import Any

# Every message is stamped from one clock, so a stamp says which of two
# messages is newer whichever notifier holds it.
_clock = itertools.count(1)
# The stamp of the last message each Wait on Notification node received, by
# the node's name: a node returns a message only once, then waits for a newer one.
_received: dict[str, int] = {}
_named: dict[str, Notifier] = {}
_named_lock = threading.Lock()


class Notifier:
    """A notifier: the latest message sent to it, shared by every function that
    holds a reference. ``ReferenceError`` is raised for a notifier that was
    released, and for a function waiting on it when it is."""

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._condition = threading.Condition()
        self._references = 1
        self._valid = True
        self._message: Any = None
        self._stamp = 0  # 0: no message
        self._waiting = 0

    @classmethod
    def obtain(
        cls, name: str = "", create: bool = True
    ) -> tuple[Notifier | None, bool]:
        """``(notifier, created new?)``. A name finds the notifier of that name
        in this program and gives another reference to it; no name, or a name
        nothing holds, makes a new one when ``create`` is true. ``None`` is the
        invalid reference of a name nothing holds when ``create`` is false."""
        if not name:
            return cls(), True
        with _named_lock:
            found = _named.get(name)
            if found is not None:
                found._references += 1
                return found, False
            if not create:
                return None, False
            made = _named[name] = cls(name)
            return made, True

    def _check(self) -> None:
        if not self._valid:
            raise ReferenceError("the notifier was released")

    def send(self, message: Any) -> None:
        """Replace the message and wake every function waiting."""
        with self._condition:
            self._check()
            self._message = message
            self._stamp = next(_clock)
            self._condition.notify_all()

    def wait(
        self,
        site: str,
        ignore_previous: bool = False,
        timeout_ms: int = -1,
        default: Any = None,
    ) -> tuple[Any, bool]:
        """``(notification, timed out?)`` for the Wait on Notification node named
        ``site``. The node returns the message the notifier holds only if it has
        not received that message before; otherwise it waits for a new one, and
        with ``ignore_previous`` it always does. A negative ``timeout_ms`` waits
        for ever; past the timeout the notification is ``default``."""
        deadline = None if timeout_ms < 0 else time.monotonic() + timeout_ms / 1000
        with self._condition:
            self._check()
            newer_than = next(_clock) if ignore_previous else _received.get(site, 0)
            self._waiting += 1
            try:
                arrived = self._condition.wait_for(
                    lambda: not self._valid or self._stamp > newer_than,
                    None if deadline is None else max(deadline - time.monotonic(), 0),
                )
            finally:
                self._waiting -= 1
            self._check()
            if not arrived:
                return default, True
            _received[site] = self._stamp
            return self._message, False

    def status(self, default: Any = None) -> tuple[str, Any, int]:
        """``(name, latest notification, number waiting)``; the latest
        notification is ``default`` when there is none."""
        with self._condition:
            self._check()
            return self.name, self._message if self._stamp else default, self._waiting

    def cancel(self, default: Any = None) -> Any:
        """Erase the message and return it (``default`` when there was none);
        a function that already received it keeps going, and a later wait
        waits for a new message."""
        with self._condition:
            self._check()
            cancelled = self._message if self._stamp else default
            self._message = None
            self._stamp = 0
            return cancelled

    def release(self, force: bool = False, default: Any = None) -> tuple[str, Any]:
        """``(name, last notification)`` (``default`` when there is none). The
        notifier is destroyed when every reference obtained is released, or at
        once with ``force``; a function waiting on it then stops waiting."""
        with _named_lock, self._condition:
            self._check()
            last = self._message if self._stamp else default
            self._references = 0 if force else self._references - 1
            if self._references <= 0:
                self._valid = False
                if _named.get(self.name) is self:
                    del _named[self.name]
                self._condition.notify_all()
            return self.name, last
'''
