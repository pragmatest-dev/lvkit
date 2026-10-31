"""Capture what one thread writes to stdout or stderr, without touching other
threads.

``sys.stdout`` and ``sys.stderr`` are process-wide (and an MCP server's stdout is
its transport), so ``capture_stdout`` / ``capture_stderr`` install (once) a proxy
that sends a write to the capturing thread's sink when that thread set one, and
to the real stream otherwise. pylabview reports a block it could not read by
printing to ``sys.stderr``; ``unresolved`` runs generation, which narrates on
stdout, and keeps that off the screen.
"""

from __future__ import annotations

import contextlib
import contextvars
import io
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal, TextIO

_sinks: dict[str, contextvars.ContextVar[io.StringIO | None]] = {
    "stdout": contextvars.ContextVar("stdout_sink", default=None),
    "stderr": contextvars.ContextVar("stderr_sink", default=None),
}


class _StreamProxy:
    def __init__(self, real: TextIO, name: str) -> None:
        self._real = real
        self._name = name

    def write(self, text: str) -> int:
        sink = _sinks[self._name].get()
        if sink is not None:
            return sink.write(text)
        return self._real.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@contextmanager
def _capture(name: Literal["stdout", "stderr"]) -> Iterator[io.StringIO]:
    if not isinstance(getattr(sys, name), _StreamProxy):
        setattr(sys, name, _StreamProxy(getattr(sys, name), name))
    sink = io.StringIO()
    token = _sinks[name].set(sink)
    try:
        yield sink
    finally:
        _sinks[name].reset(token)


def capture_stderr() -> contextlib.AbstractContextManager[io.StringIO]:
    """Collect what the calling thread writes to stderr inside the block."""
    return _capture("stderr")


def capture_stdout() -> contextlib.AbstractContextManager[io.StringIO]:
    """Collect what the calling thread writes to stdout inside the block."""
    return _capture("stdout")
