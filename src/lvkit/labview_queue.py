"""LabVIEW Queue Operations runtime.

Models LabVIEW's "Queue Operations" palette (Obtain Queue, Enqueue Element,
Enqueue Element At Opposite End, Dequeue Element, Get Queue Status, Release
Queue) on Python's stdlib — only ``threading`` and ``collections.deque``.

The mapping keeps Pythonic building blocks but honours the LabVIEW behaviours
that generated code *branches on* (verified against NI's function docs):

- **`deque` + `threading.Condition`, not `queue.Queue`.** LabVIEW inserts at
  EITHER end (Enqueue Element = back, Enqueue Element At Opposite End = front)
  while Dequeue always removes the front; ``queue.Queue`` is push-to-back only.
- **Bounded = blocking, not lossy.** ``max_size`` -1 is unbounded; when bounded
  and full, Enqueue *waits* for a Dequeue (NI: it "waits until ... functions
  remove elements"). Lossy Enqueue is a separate LabVIEW function.
- **Timeouts return a ``timed_out`` bool, not an exception.** LabVIEW dataflow
  tests the "timed out?" terminal; generated code mirrors that. (-1 blocks, 0 is
  one non-blocking attempt, positive waits up to N ms.)
- **Reference-counted lifecycle.** Obtain returns a new reference to a shared
  named queue (process-wide); Release Queue with ``force_destroy=False``
  (default) releases ONE reference and destroys the queue only when the last
  reference is released; ``force_destroy=True`` destroys it immediately and
  invalidates ALL references.
- **Destroy is a control signal.** Any op on a destroyed queue — including one
  waiting on a Dequeue/Enqueue when it is force-destroyed — raises
  ``LabVIEWError(1122)`` (NI: pending functions "time out and return error code
  1122"). LabVIEW code relies on this to end consumer loops.
- **`create_if_not_found=False`** on a missing named queue raises
  ``LabVIEWError(1100)`` (NI: "no object of that name was found").
- **`queue_out` is a passthrough** — the reference never changes across an
  Enqueue/Dequeue/Status call (codegen binds it to the input queue variable).
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .labview_error import LabVIEWError

# NI-confirmed error codes (docs-be.ni.com obtain-queue / release-queue pages).
_ERR_QUEUE_NOT_FOUND = 1100   # Obtain, create_if_not_found=False, no such queue
_ERR_QUEUE_DESTROYED = 1122   # any op on a destroyed / invalid queue reference

# Named queues are process-wide; the registry maps name -> the single shared
# LVQueue. Lock ordering everywhere that takes both is REGISTRY then the queue's
# own Condition, so obtain/release never deadlock against enqueue/dequeue (which
# take only the Condition, and release it while waiting).
_QUEUE_REGISTRY: dict[str, LVQueue] = {}
_REGISTRY_LOCK = threading.Lock()


@dataclass
class QueueStatus:
    """The Get Queue Status output pane (NI terminal order)."""

    max_size: int
    name: str
    pending_remove: int
    pending_insert: int
    n_elements: int
    elements: list[object] = field(default_factory=list)


class LVQueue:
    """A LabVIEW queue reference (refnum).

    Backed by a ``deque`` so elements insert at either end while ``dequeue``
    always removes the front — one class covers Enqueue Element (back), Enqueue
    Element At Opposite End (front), and FIFO Dequeue. All mutable state
    (items, refcount, destroyed flag, pending-op counters) is guarded by
    ``_condition``.
    """

    def __init__(self, name: str = "", max_size: int = -1) -> None:
        self.name = name
        self.max_size = max_size
        self._items: deque[object] = deque()
        self._condition = threading.Condition()
        self._destroyed = False
        self._refcount = 1
        self._pending_remove = 0
        self._pending_insert = 0

    def _raise_if_destroyed(self) -> None:
        if self._destroyed:
            raise LabVIEWError(
                code=_ERR_QUEUE_DESTROYED,
                source="Queue",
                message="the queue reference is not valid (the queue was destroyed)",
            )

    def _has_room(self) -> bool:
        return self.max_size < 0 or len(self._items) < self.max_size

    def enqueue(self, element: object, timeout_ms: int, *, front: bool = False) -> bool:
        """Add ``element`` (back, or front when ``front``). Returns ``timed_out``.

        Blocks while the queue is full (bounded); raises ``LabVIEWError(1122)``
        if the queue is/becomes destroyed while waiting."""
        deadline = _deadline(timeout_ms)
        with self._condition:
            self._raise_if_destroyed()
            while not self._has_room():
                self._pending_insert += 1
                try:
                    woke = _wait(self._condition, timeout_ms, deadline)
                finally:
                    self._pending_insert -= 1
                self._raise_if_destroyed()
                if not woke:
                    return True
            (self._items.appendleft if front else self._items.append)(element)
            self._condition.notify_all()
            return False

    def dequeue(self, timeout_ms: int) -> tuple[object, bool]:
        """Remove and return the front element as ``(element, timed_out)``.

        Blocks while empty; raises ``LabVIEWError(1122)`` if the queue
        is/becomes destroyed while waiting."""
        deadline = _deadline(timeout_ms)
        with self._condition:
            self._raise_if_destroyed()
            while not self._items:
                self._pending_remove += 1
                try:
                    woke = _wait(self._condition, timeout_ms, deadline)
                finally:
                    self._pending_remove -= 1
                self._raise_if_destroyed()
                if not woke:
                    return None, True
            element = self._items.popleft()
            self._condition.notify_all()
            return element, False

    def status(self, return_elements: bool) -> QueueStatus:
        """The Get Queue Status pane. ``elements`` is filled only when asked
        (LabVIEW's ``return elements?`` defaults FALSE)."""
        with self._condition:
            self._raise_if_destroyed()
            return QueueStatus(
                max_size=self.max_size,
                name=self.name,
                pending_remove=self._pending_remove,
                pending_insert=self._pending_insert,
                n_elements=len(self._items),
                elements=list(self._items) if return_elements else [],
            )


def _deadline(timeout_ms: int) -> float | None:
    """Absolute monotonic deadline for a wait, or None to block forever."""
    if timeout_ms < 0:
        return None
    return time.monotonic() + timeout_ms / 1000.0


def _wait(
    condition: threading.Condition, timeout_ms: int, deadline: float | None,
) -> bool:
    """Wait on ``condition`` per LabVIEW timeout semantics.

    Returns False (caller reports ``timed_out=True``) if the wait expired
    without a notify; True otherwise (the caller re-checks its while-condition
    and the destroyed flag against spurious wakeups)."""
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
    """Obtain (or create) a queue reference. Returns ``(queue, created)``.

    An empty name is always a fresh, unshared queue. A named queue is shared
    process-wide: an existing one gets a new reference (refcount +1); a missing
    one is created only when ``create_if_not_found`` (else ``LabVIEWError(1100)``).
    """
    if not name:
        return LVQueue(name=name, max_size=max_size), True
    with _REGISTRY_LOCK:
        existing = _QUEUE_REGISTRY.get(name)
        if existing is not None:
            with existing._condition:
                existing._refcount += 1
            return existing, False
        if not create_if_not_found:
            raise LabVIEWError(
                code=_ERR_QUEUE_NOT_FOUND,
                source="Obtain Queue",
                message=(
                    f"queue {name!r} does not exist and "
                    "create-if-not-found is false"
                ),
            )
        created = LVQueue(name=name, max_size=max_size)
        _QUEUE_REGISTRY[name] = created
        return created, True


def enqueue_element(queue: LVQueue, element: object, timeout_ms: int) -> bool:
    """Add ``element`` to the back of ``queue``. Returns ``timed_out``."""
    return queue.enqueue(element, timeout_ms, front=False)


def enqueue_element_at_opposite_end(
    queue: LVQueue, element: object, timeout_ms: int,
) -> bool:
    """Add ``element`` to the front of ``queue``. Returns ``timed_out``."""
    return queue.enqueue(element, timeout_ms, front=True)


def dequeue_element(queue: LVQueue, timeout_ms: int) -> tuple[object, bool]:
    """Remove and return the front element as ``(element, timed_out)``."""
    return queue.dequeue(timeout_ms)


def get_queue_status(queue: LVQueue, return_elements: bool = False) -> QueueStatus:
    """Snapshot the queue without removing anything (NI ``return elements?``
    defaults FALSE)."""
    return queue.status(return_elements)


def release_queue(queue: LVQueue, force_destroy: bool = False) -> list[object]:
    """Release one reference to ``queue`` (or destroy it outright).

    ``force_destroy=False`` (default) decrements the reference count and
    destroys the queue only when the last reference is released;
    ``force_destroy=True`` destroys it immediately, invalidating every
    reference. Returns the elements that remained in the queue when it was
    destroyed (front-to-back), or ``[]`` if it survives. Any op waiting on the
    destroyed queue wakes and raises ``LabVIEWError(1122)``.
    """
    with _REGISTRY_LOCK:
        with queue._condition:
            if queue._destroyed:
                return []
            queue._refcount -= 1
            if not (force_destroy or queue._refcount <= 0):
                return []
            queue._destroyed = True
            if queue.name:
                _QUEUE_REGISTRY.pop(queue.name, None)
            remaining = list(queue._items)
            queue._items.clear()
            queue._condition.notify_all()
            return remaining
