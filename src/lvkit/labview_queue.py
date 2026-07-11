"""LabVIEW Queue Operations runtime.

Models LabVIEW's "Queue Operations" palette (Obtain Queue, Enqueue Element,
Enqueue Element At Opposite End, Dequeue Element, Get Queue Status) on
Python's stdlib. No third-party dependencies — only `threading` and
`collections.deque`.

Design decisions (see scratchpad/phaseB_findings.md for the full writeup):

- **Single deque-backed queue, not `queue.Queue`.** LabVIEW needs
  insertion at EITHER end (Enqueue Element = back, Enqueue Element At
  Opposite End = front) while Dequeue Element always removes from the
  front. `queue.Queue` only supports FIFO push-to-back — it cannot push
  to the front — so `LVQueue` wraps a `collections.deque` directly with
  a `threading.Condition` guarding both ends.
- **Named queues share ONE instance.** LabVIEW's Obtain Queue returns the
  SAME queue reference for every call with the same non-empty `name`
  (process-wide). A module-level registry keyed by name implements this;
  an empty name always creates a fresh, unshared queue (matches "name
  (unnamed)" default in the LabVIEW docs).
- **`timeout_ms` semantics** (matches the LabVIEW "timeout in ms (-1)"
  docs): negative blocks indefinitely, 0 is exactly one non-blocking
  attempt, positive waits up to that many milliseconds. Enqueue/Dequeue
  report `timed_out=True` on timeout rather than raising.
- **`queue_out` is a passthrough.** Per NI docs ("queue out returns the
  reference to the queue unchanged"), the queue reference itself never
  changes across an Enqueue/Dequeue call — codegen binds `queue_out`
  directly to the input queue variable rather than emitting a statement.
- **`create_if_not_found=False`** on a named queue that doesn't exist yet
  is documented by NI to be an error condition, but this runtime does
  not have a verified LabVIEW error code for it (fabricating one would
  violate this project's "never guess" rule) -- as a conservative
  first cut, `obtain_queue` always creates the queue when missing,
  regardless of `create_if_not_found`. Flagged for follow-up.
"""

from __future__ import annotations

import threading
import time
from collections import deque

_QUEUE_REGISTRY: dict[str, LVQueue] = {}
_REGISTRY_LOCK = threading.Lock()


class LVQueue:
    """A LabVIEW queue reference (refnum).

    Backed by a `deque` so elements can be inserted at either end while
    `dequeue` always removes from the front — this is what lets a single
    class implement both Enqueue Element (back) and Enqueue Element At
    Opposite End (front) while keeping Dequeue Element's FIFO-from-front
    behavior for both.
    """

    def __init__(self, name: str = "", max_size: int = -1) -> None:
        self.name = name
        self.max_size = max_size
        self._items: deque[object] = deque()
        self._condition = threading.Condition()

    def _has_room(self) -> bool:
        return self.max_size < 0 or len(self._items) < self.max_size

    def enqueue(self, element: object, timeout_ms: int, *, front: bool = False) -> bool:
        """Add `element` to the queue. Returns True if the call timed out."""
        deadline = _deadline(timeout_ms)
        with self._condition:
            while not self._has_room():
                if not _wait(self._condition, timeout_ms, deadline):
                    return True
            if front:
                self._items.appendleft(element)
            else:
                self._items.append(element)
            self._condition.notify_all()
            return False

    def dequeue(self, timeout_ms: int) -> tuple[object, bool]:
        """Remove and return the front element as (element, timed_out)."""
        deadline = _deadline(timeout_ms)
        with self._condition:
            while not self._items:
                if not _wait(self._condition, timeout_ms, deadline):
                    return None, True
            element = self._items.popleft()
            self._condition.notify_all()
            return element, False

    def status(self) -> tuple[str, list[object]]:
        """Return (name, pending elements) without removing anything."""
        with self._condition:
            return self.name, list(self._items)


def _deadline(timeout_ms: int) -> float | None:
    """Absolute monotonic deadline for a wait, or None to block forever."""
    if timeout_ms < 0:
        return None
    return time.monotonic() + timeout_ms / 1000.0


def _wait(
    condition: threading.Condition, timeout_ms: int, deadline: float | None,
) -> bool:
    """Wait on `condition` per LabVIEW timeout semantics.

    Returns False (caller should report timed_out=True) if the wait
    expired without the condition being notified; True otherwise (the
    caller re-checks its while-condition to guard against spurious
    wakeups, standard `threading.Condition` usage).
    """
    if timeout_ms == 0:
        return False  # non-blocking: the one check already failed
    if deadline is None:
        condition.wait()
        return True
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    return condition.wait(timeout=remaining)


def obtain_queue(
    name: str, max_size: int, create_if_not_found: bool = True,
) -> tuple[LVQueue, bool]:
    """Obtain (or create) a queue reference. Returns (queue, created).

    See module docstring: `create_if_not_found=False` on a missing named
    queue is not currently enforced (no verified LabVIEW error code) —
    the queue is created regardless.
    """
    del create_if_not_found  # not enforced yet -- see module docstring
    if not name:
        return LVQueue(name=name, max_size=max_size), True
    with _REGISTRY_LOCK:
        existing = _QUEUE_REGISTRY.get(name)
        if existing is not None:
            return existing, False
        created = LVQueue(name=name, max_size=max_size)
        _QUEUE_REGISTRY[name] = created
        return created, True


def enqueue_element(queue: LVQueue, element: object, timeout_ms: int) -> bool:
    """Add `element` to the back of `queue`. Returns timed_out."""
    return queue.enqueue(element, timeout_ms, front=False)


def enqueue_element_at_opposite_end(
    queue: LVQueue, element: object, timeout_ms: int,
) -> bool:
    """Add `element` to the front of `queue`. Returns timed_out."""
    return queue.enqueue(element, timeout_ms, front=True)


def dequeue_element(queue: LVQueue, timeout_ms: int) -> tuple[object, bool]:
    """Remove and return the front element as (element, timed_out)."""
    return queue.dequeue(timeout_ms)


def get_queue_status(queue: LVQueue, return_elements: bool) -> tuple[str, list[object]]:
    """Return (name, elements). `elements` is [] unless return_elements."""
    name, elements = queue.status()
    return name, (elements if return_elements else [])
