"""The ``notifiers`` module a generated package holds, run on its own with threads,
and the rule that it is written only when a generated module imports it."""

from __future__ import annotations

import threading
import time
import types
from pathlib import Path

import pytest

from lvkit.codegen.notifiers_module import NOTIFIERS_MODULE_SOURCE
from lvkit.pipeline import _write_notifiers_module


def helper() -> types.ModuleType:
    """A fresh copy of the generated ``notifiers`` module."""
    module = types.ModuleType("notifiers")
    exec(compile(NOTIFIERS_MODULE_SOURCE, "notifiers.py", "exec"), module.__dict__)  # noqa: S102
    return module


def in_thread(call):
    """Run ``call`` in a thread; the list its result lands in."""
    result: list = []
    thread = threading.Thread(target=lambda: result.append(call()))
    thread.start()
    return thread, result


def test_send_then_wait_returns_the_value() -> None:
    notifier, created = helper().Notifier.obtain()
    assert created is True
    notifier.send(7)
    assert notifier.wait("a") == (7, False)


def test_wait_with_a_timeout_and_no_send_times_out() -> None:
    notifier, _ = helper().Notifier.obtain()
    started = time.monotonic()
    assert notifier.wait("a", timeout_ms=50, default=-1) == (-1, True)
    assert time.monotonic() - started >= 0.04


def test_two_waiters_both_wake_on_one_send() -> None:
    notifier, _ = helper().Notifier.obtain()
    first = in_thread(lambda: notifier.wait("first", timeout_ms=5000))
    second = in_thread(lambda: notifier.wait("second", timeout_ms=5000))
    while notifier.status()[2] < 2:
        time.sleep(0.005)
    notifier.send("go")
    for thread, result in (first, second):
        thread.join(5)
        assert result == [("go", False)]


def test_a_node_returns_a_message_once_then_waits_for_a_new_one() -> None:
    """NI: each Wait on Notification node remembers the last message it received."""
    notifier, _ = helper().Notifier.obtain()
    notifier.send(1)
    assert notifier.wait("node", timeout_ms=0) == (1, False)
    assert notifier.wait("node", timeout_ms=0, default=0) == (0, True)
    assert notifier.wait("other node", timeout_ms=0) == (1, False)
    notifier.send(2)
    assert notifier.wait("node", timeout_ms=0) == (2, False)


def test_ignore_previous_false_returns_an_already_sent_message_at_once() -> None:
    notifier, _ = helper().Notifier.obtain()
    notifier.send(5)
    assert notifier.wait("a", ignore_previous=False, timeout_ms=0) == (5, False)


def test_ignore_previous_true_waits_for_the_next_message() -> None:
    notifier, _ = helper().Notifier.obtain()
    notifier.send(5)
    assert notifier.wait("a", ignore_previous=True, timeout_ms=30, default=0) == (
        0,
        True,
    )
    thread, result = in_thread(
        lambda: notifier.wait("a", ignore_previous=True, timeout_ms=5000)
    )
    while notifier.status()[2] < 1:
        time.sleep(0.005)
    notifier.send(6)
    thread.join(5)
    assert result == [(6, False)]


def test_a_wait_does_not_remove_the_message_from_other_nodes() -> None:
    notifier, _ = helper().Notifier.obtain()
    notifier.send(3)
    assert notifier.wait("a") == (3, False)
    assert notifier.wait("b") == (3, False)


def test_wait_on_a_released_notifier_raises_reference_error() -> None:
    notifier, _ = helper().Notifier.obtain()
    notifier.release()
    with pytest.raises(ReferenceError):
        notifier.wait("a", timeout_ms=0)


def test_releasing_a_notifier_ends_a_wait_with_reference_error() -> None:
    notifier, _ = helper().Notifier.obtain()
    errors: list[BaseException] = []

    def wait() -> None:
        try:
            notifier.wait("a", timeout_ms=5000)
        except ReferenceError as error:
            errors.append(error)

    thread = threading.Thread(target=wait)
    thread.start()
    while notifier.status()[2] < 1:
        time.sleep(0.005)
    notifier.release()
    thread.join(5)
    assert len(errors) == 1


def test_the_same_name_is_the_same_notifier() -> None:
    notifiers = helper()
    first, created_first = notifiers.Notifier.obtain("Shared")
    second, created_second = notifiers.Notifier.obtain("Shared")
    assert first is second
    assert (created_first, created_second) == (True, False)
    other, _ = notifiers.Notifier.obtain("Other")
    assert other is not first
    unnamed, _ = notifiers.Notifier.obtain()
    again, _ = notifiers.Notifier.obtain()
    assert unnamed is not again


def test_a_name_nothing_holds_is_not_created_when_create_is_false() -> None:
    notifier, created = helper().Notifier.obtain("Nothing", create=False)
    assert (notifier, created) == (None, False)


def test_a_named_notifier_is_destroyed_by_as_many_releases_as_obtains() -> None:
    notifiers = helper()
    first, _ = notifiers.Notifier.obtain("Shared")
    second, _ = notifiers.Notifier.obtain("Shared")
    first.release()
    second.send(1)  # still there
    second.release()
    with pytest.raises(ReferenceError):
        second.send(2)
    fresh, created = notifiers.Notifier.obtain("Shared")
    assert created is True
    assert fresh is not first


def test_force_destroy_releases_every_reference() -> None:
    notifiers = helper()
    first, _ = notifiers.Notifier.obtain("Shared")
    notifiers.Notifier.obtain("Shared")
    first.release(force=True)
    with pytest.raises(ReferenceError):
        first.send(1)


def test_release_returns_the_name_and_the_last_message() -> None:
    notifiers = helper()
    notifier, _ = notifiers.Notifier.obtain("Name")
    assert notifier.release(default="none") == ("Name", "none")
    again, _ = notifiers.Notifier.obtain("Name")
    again.send("last")
    assert again.release(default="none") == ("Name", "last")


def test_status_and_cancel() -> None:
    notifier, _ = helper().Notifier.obtain("Status")
    assert notifier.status(default=0) == ("Status", 0, 0)
    notifier.send(4)
    assert notifier.status() == ("Status", 4, 0)
    assert notifier.cancel() == 4
    assert notifier.cancel(default=-1) == -1
    assert notifier.wait("a", timeout_ms=0, default=0) == (0, True)


def test_notifiers_py_is_written_only_when_a_module_imports_it(tmp_path: Path) -> None:
    (tmp_path / "plain.py").write_text("def f():\n    return 1\n")
    _write_notifiers_module(tmp_path)
    assert not (tmp_path / "notifiers.py").exists()
    (tmp_path / "uses.py").write_text("from .notifiers import Notifier\n")
    _write_notifiers_module(tmp_path)
    assert (tmp_path / "notifiers.py").read_text() == NOTIFIERS_MODULE_SOURCE
