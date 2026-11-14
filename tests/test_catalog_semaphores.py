"""Acquire Semaphore and Release Semaphore: each caller diagram is generated from
the catalog entry the recorded path names (the connector pane numbers are the ones
the corpus callers record), and its Python is run on a ``threading`` semaphore.

NI's pages: Acquire waits ``ms timeout`` milliseconds (-1 never times out), counts
every execution, and gives ``timed out`` TRUE when the semaphore did not become
available; Release returns error 1111 when the semaphore is not currently acquired,
which a ``BoundedSemaphore`` of the semaphore's size reports as ``ValueError``."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.vilib_caller import BOOL, I32, primitive, run_catalog_call

SEMAPHORE = primitive("Refnum")
LIBRARY = ["<vilib>", "Utility", "semaphor.llb"]
ACQUIRE_PANE = [
    (0, "output", ERROR_CLUSTER),
    (1, "output", BOOL),
    (3, "output", SEMAPHORE),
    (4, "input", ERROR_CLUSTER),
    (5, "input", I32),
    (7, "input", SEMAPHORE),
]
RELEASE_PANE = [
    (0, "output", ERROR_CLUSTER),
    (3, "output", SEMAPHORE),
    (4, "input", ERROR_CLUSTER),
    (7, "input", SEMAPHORE),
]


def _acquire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semaphore: threading.BoundedSemaphore,
    **given: int,
) -> Any:
    inputs = {"semaphore": (7, SEMAPHORE)}
    if given:
        inputs["ms"] = (5, I32)
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LIBRARY, "Acquire Semaphore.vi"],
        ACQUIRE_PANE,
        inputs,
        {"timed_out": (1, BOOL), "out": (3, SEMAPHORE)},
        {"semaphore": semaphore, **({"ms": given["ms"]} if given else {})},
    )
    return result


def _release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semaphore: threading.BoundedSemaphore,
) -> Any:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*LIBRARY, "Release Semaphore_71.vi"],
        RELEASE_PANE,
        {"semaphore": (7, SEMAPHORE)},
        {"out": (3, SEMAPHORE), "error": (0, ERROR_CLUSTER)},
        {"semaphore": semaphore},
    )
    return result


def test_acquire_takes_the_semaphore_and_says_it_did_not_time_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    semaphore = threading.BoundedSemaphore(2)
    first = _acquire(tmp_path, monkeypatch, semaphore)
    assert first.timed_out is False
    assert first.out is semaphore
    # Every execution counts, so a second acquire takes the second place.
    assert _acquire(tmp_path, monkeypatch, semaphore, ms=0).timed_out is False
    assert semaphore.acquire(blocking=False) is False


def test_acquire_times_out_after_ms_timeout_milliseconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    started = time.monotonic()
    result = _acquire(tmp_path, monkeypatch, semaphore, ms=100)
    assert result.timed_out is True
    assert 0.09 <= time.monotonic() - started < 2


def test_a_timeout_of_minus_one_waits_until_the_semaphore_is_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    threading.Timer(0.1, semaphore.release).start()
    assert _acquire(tmp_path, monkeypatch, semaphore, ms=-1).timed_out is False


def test_the_default_timeout_is_never(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    threading.Timer(0.1, semaphore.release).start()
    assert _acquire(tmp_path, monkeypatch, semaphore).timed_out is False


def test_release_gives_the_place_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    result = _release(tmp_path, monkeypatch, semaphore)
    assert result.error is None
    assert result.out is semaphore
    assert semaphore.acquire(blocking=False) is True


def test_release_wakes_an_acquire_that_is_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    threading.Timer(0.1, semaphore.release).start()
    assert _acquire(tmp_path, monkeypatch, semaphore, ms=5000).timed_out is False


def test_release_of_a_semaphore_that_is_not_acquired_is_error_1111(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _release(tmp_path, monkeypatch, threading.BoundedSemaphore(1))
    assert result.error is not None
    assert result.error.code == 1111
