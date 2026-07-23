"""LabVIEW queue runtime: reference-counting, destroy semantics, NI error codes.

Executes the runtime directly (the generated code calls these same functions).
Error codes and behaviours are verified against NI's Obtain/Release/Get-Status
function docs: bounded=blocking, refcounted lifecycle, force-destroy invalidates
all refs (pending ops -> 1122), create-if-not-found FALSE -> 1100.
"""
from __future__ import annotations

import threading
import time

import pytest

import lvkit.labview_queue as lq
from lvkit.labview_error import LabVIEWError
from lvkit.labview_queue import (
    QueueStatus,
    dequeue_element,
    enqueue_element,
    get_queue_status,
    obtain_queue,
    release_queue,
)


@pytest.fixture(autouse=True)
def _clear_registry():
    lq._QUEUE_REGISTRY.clear()
    yield
    lq._QUEUE_REGISTRY.clear()


def test_named_queue_is_shared_and_reference_counted():
    q1, created1 = obtain_queue("data", -1)
    q2, created2 = obtain_queue("data", -1)
    assert q1 is q2
    assert created1 is True and created2 is False
    # two references: releasing one keeps the queue alive
    assert release_queue(q1) == []
    enqueue_element(q2, 42, 0)
    assert dequeue_element(q2, 0) == (42, False)
    # releasing the last reference destroys it
    release_queue(q2)
    with pytest.raises(LabVIEWError) as ei:
        enqueue_element(q2, 1, 0)
    assert ei.value.code == 1122


def test_force_destroy_invalidates_all_references_and_returns_remaining():
    q1, _ = obtain_queue("j", -1)
    q2, _ = obtain_queue("j", -1)
    enqueue_element(q1, "a", 0)
    enqueue_element(q1, "b", 0)
    remaining = release_queue(q1, force_destroy=True)
    assert remaining == ["a", "b"]  # front-to-back
    for q in (q1, q2):
        with pytest.raises(LabVIEWError) as ei:
            dequeue_element(q, 0)
        assert ei.value.code == 1122


def test_create_if_not_found_false_raises_1100():
    with pytest.raises(LabVIEWError) as ei:
        obtain_queue("missing", -1, create_if_not_found=False)
    assert ei.value.code == 1100


def test_get_queue_status_full_pane():
    q, _ = obtain_queue("s", 5)
    enqueue_element(q, 1, 0)
    enqueue_element(q, 2, 0)
    st = get_queue_status(q, return_elements=True)
    assert isinstance(st, QueueStatus)
    assert (st.max_size, st.name, st.n_elements) == (5, "s", 2)
    assert st.elements == [1, 2]
    assert st.pending_remove == 0 and st.pending_insert == 0
    # return_elements defaults FALSE: count still exact, elements withheld
    st2 = get_queue_status(q)
    assert st2.elements == [] and st2.n_elements == 2


def test_force_destroy_wakes_a_blocked_dequeue_with_1122():
    q, _ = obtain_queue("blk", -1)
    errors: list[int] = []

    def consumer():
        try:
            dequeue_element(q, -1)  # blocks forever on an empty queue
        except LabVIEWError as e:
            errors.append(e.code)

    t = threading.Thread(target=consumer)
    t.start()
    time.sleep(0.05)  # let the consumer block
    release_queue(q, force_destroy=True)
    t.join(timeout=2.0)
    assert errors == [1122]


def test_bounded_queue_is_blocking_not_lossy():
    q, _ = obtain_queue("", 1)  # unnamed, bounded to one element
    assert enqueue_element(q, "a", 0) is False   # fits
    assert enqueue_element(q, "b", 0) is True     # full -> timed_out, "a" kept
    assert dequeue_element(q, 0) == ("a", False)
