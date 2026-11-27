"""An operation on a reference that was never made (Python's ``None``, LabVIEW's
Not a Refnum) gives an error on its error out, and its outputs take their defaults;
a live reference works as before.

Error 1 "An input parameter is invalid." is the code LabVIEW reports for a
Not-a-Refnum queue (Enqueue Element), user event (Generate User Event) and notifier
input: NI's General LabVIEW Error Codes lists the code, and NI's forum threads
"error 1 enqueue" (forums.ni.com/t5/LabVIEW/error-1-enqueue/td-p/2141428) and
"Error 1 at Generate User Event"
(forums.ni.com/t5/LabVIEW/Error-1-at-Generate-User-Event/m-p/2170746) show it. A
data value reference is error 1556, "The reference is invalid" (NI's list)."""

from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.error_diagrams import (
    ERR,
    I32,
    NOTIFIER,
    QUEUE,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.test_catalog_semaphores import ACQUIRE_PANE, LIBRARY, SEMAPHORE
from tests.user_event_diagrams import generate_user_event, user_event_of
from tests.vilib_caller import BOOL as CATALOG_BOOL
from tests.vilib_caller import run_catalog_call

NAME = "Err.vi"
SEND, ENQUEUE, DELETE_DVR = 9104, 9111, 2409


def _ref_vi(prim_id: int, ref_type: int, slots: list[tuple[str, int, int, bool]]) -> Vi:
    """A control ``ref`` (and ``value``, an I32) wired to the primitive's first
    reference input (and its second input), the error wire through it."""
    vi = Vi(NAME)
    ref = vi.control("ref", ref_type, REQUIRED)
    value = vi.control("value", I32, REQUIRED)
    ein = vi.error_in()
    eout = vi.error_out()
    node = vi.primitive(prim_id, slots)
    vi.wire(ref, node["ref"])
    if "value" in node:
        vi.wire(value, node["value"])
    vi.wire(ein, node["error in"])
    vi.wire(node["error out"], eout)
    return vi


def _generated(project: Project, vi: Vi):  # noqa: F811
    project.add(vi)
    return project.generate(NAME)


def _assert_invalid(result, code: int, source: str) -> None:
    assert result.error_out.status is True
    assert result.error_out.code == code
    assert result.error_out.source == source


def test_send_notification_on_a_notifier_never_made_is_error_1(
    project: Project,  # noqa: F811
) -> None:
    vi = _ref_vi(
        SEND,
        NOTIFIER,
        [
            ("ref", 0, NOTIFIER, False),
            ("value", 1, I32, False),
            ("error in", 3, ERR, False),
            ("notifier out", 8, NOTIFIER, True),
            ("error out", 11, ERR, True),
        ],
    )
    package = _generated(project, vi)
    result = package.function(ref=None, value=1)
    _assert_invalid(result, 1, "Send Notification")
    sent: list[int] = []
    live = SimpleNamespace(send=sent.append)
    assert package.function(ref=live, value=1).error_out is None
    assert sent == [1]


def test_enqueue_on_a_queue_never_made_is_error_1(
    project: Project,  # noqa: F811
) -> None:
    vi = _ref_vi(
        ENQUEUE,
        QUEUE,
        [
            ("ref", 0, QUEUE, False),
            ("value", 1, I32, False),
            ("error in", 3, ERR, False),
            ("queue out", 8, QUEUE, True),
            ("error out", 11, ERR, True),
        ],
    )
    package = _generated(project, vi)
    result = package.function(ref=None, value=1)
    _assert_invalid(result, 1, "Enqueue Element")
    live: queue.Queue = queue.Queue()
    assert package.function(ref=live, value=1).error_out is None
    assert live.get_nowait() == 1


def test_generate_user_event_on_one_never_made_is_error_1(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    event_type = user_event_of(vi, "Stop", I32)
    ref = vi.control("ref", event_type, REQUIRED)
    value = vi.control("value", I32, REQUIRED)
    ein = vi.error_in()
    eout = vi.error_out()
    node = generate_user_event(vi, event_type, I32)
    vi.wire(ref, node["user event"])
    vi.wire(value, node["data"])
    vi.wire(ein, node["error in"])
    vi.wire(node["error out"], eout)
    package = _generated(project, vi)
    result = package.function(ref=None, value=1)
    _assert_invalid(result, 1, "Generate User Event")
    holder: queue.Queue = queue.Queue()
    assert package.function(ref=[holder], value=5).error_out is None
    assert holder.get_nowait()[1] == 5


def test_delete_data_value_reference_on_none_is_error_1556(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    dvr_type = vi.add_type(
        '<TypeDesc Type="Refnum" RefType="DataValueRef" Format="inline">'
        '<TypeDesc TypeID="1" /></TypeDesc>'
    )
    ref = vi.control("ref", dvr_type, REQUIRED)
    ein = vi.error_in()
    value = vi.indicator("value", I32)
    eout = vi.error_out()
    node = vi.primitive(
        DELETE_DVR,
        [
            ("ref", 7, dvr_type, False),
            ("error in", 4, ERR, False),
            ("value", 3, I32, True),
            ("error out", 0, ERR, True),
        ],
    )
    vi.wire(ref, node["ref"])
    vi.wire(ein, node["error in"])
    vi.wire(node["value"], value)
    vi.wire(node["error out"], eout)
    package = _generated(project, vi)
    result = package.function(ref=None)
    _assert_invalid(result, 1556, "Delete Data Value Reference")
    live = package.function(ref=[9])
    assert (live.value, live.error_out) == (9, None)


def test_acquire_semaphore_on_none_is_error_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LIBRARY, "Acquire Semaphore.vi"],
        ACQUIRE_PANE,
        {"semaphore": (7, SEMAPHORE)},
        {"timed_out": (1, CATALOG_BOOL), "error": (0, ERROR_CLUSTER)},
        {"semaphore": None},
    )
    assert (result.error.code, result.error.status) == (1, True)
    assert result.error.source == "Acquire Semaphore"
    assert result.timed_out is False
