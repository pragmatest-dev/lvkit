"""A ``LiveIO`` that records what a generated function writes, reads and sets,
for the tests that call a hooked function with ``live_io=``."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Any

from lvkit.panel.live_io import LiveIO


class Recorder(LiveIO):
    """``calls`` are the writes ``(name, value)``, ``reads`` the reads, and
    ``properties`` the property writes ``(name, prop, value)``, each in call
    order, and ``log`` every call as ``(method, *arguments)``. A read answers
    from ``answers`` in turn (the value the function holds once they run out),
    or from ``answer`` when one is given."""

    def __init__(
        self,
        *answers: Any,
        answer: Callable[[Enum, Any], Any] | None = None,
        events: Any = None,
    ) -> None:
        self.answers = list(answers)
        self.answer = answer
        self.events = events
        self.calls: list[tuple[Enum, Any]] = []
        self.reads: list[tuple[Enum, Any]] = []
        self.properties: list[tuple[Enum, Enum, Any]] = []
        self.log: list[tuple[Any, ...]] = []

    def write(self, name: Enum, value: Any) -> None:
        self.calls.append((name, value))
        self.log.append(("write", name, value))

    def read(self, name: Enum, value: Any) -> Any:
        self.reads.append((name, value))
        self.log.append(("read", name, value))
        if self.answer is not None:
            return self.answer(name, value)
        return self.answers.pop(0) if self.answers else value

    def set_property(self, name: Enum, prop: Enum, value: Any) -> Any:
        self.properties.append((name, prop, value))
        self.log.append(("property", name, prop, value))
        return None
