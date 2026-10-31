"""``capture_stderr`` takes only the capturing thread's writes."""

from __future__ import annotations

import io
import sys
import threading

import pytest

from lvkit.stderr_capture import capture_stderr


def test_each_thread_s_line_lands_in_the_right_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = io.StringIO()
    monkeypatch.setattr(sys, "stderr", real)
    inside = threading.Event()
    other_wrote = threading.Event()
    captured: list[str] = []

    def extracting() -> None:
        with capture_stderr() as sink:
            inside.set()
            other_wrote.wait(5)
            print("warning from pylabview", file=sys.stderr)
        captured.append(sink.getvalue())

    worker = threading.Thread(target=extracting)
    worker.start()
    inside.wait(5)
    print("log from another thread", file=sys.stderr)
    other_wrote.set()
    worker.join()

    assert captured == ["warning from pylabview\n"]
    assert real.getvalue() == "log from another thread\n"


def test_writes_outside_a_capture_reach_the_real_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = io.StringIO()
    monkeypatch.setattr(sys, "stderr", real)
    with capture_stderr():
        pass
    print("after", file=sys.stderr)
    assert real.getvalue() == "after\n"
