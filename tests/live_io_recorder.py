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
        self.property_reads: list[tuple[Enum, Enum, Any]] = []
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

    def get_property(self, name: Enum, prop: Enum, value: Any) -> Any:
        self.property_reads.append((name, prop, value))
        self.log.append(("get_property", name, prop, value))
        return value

    def open_panel(self, activate: bool = False, state: int = 1) -> None:
        self.log.append(("open_panel", activate, state))

    def close_panel(self) -> None:
        self.log.append(("close_panel",))

    def set_window_title(self, title: str) -> None:
        self.log.append(("window_title", title))

    def set_window_bounds(self, bounds: tuple[int, int, int, int]) -> None:
        self.log.append(("window_bounds", bounds))

    def center_panel(self) -> None:
        self.log.append(("center_panel",))

    def set_window_state(self, state: int) -> None:
        self.log.append(("window_state", state))

    def bring_to_front(self) -> None:
        self.log.append(("bring_to_front",))

    def is_frontmost(self, held: bool) -> bool:
        self.log.append(("is_frontmost", held))
        return held

    def set_panel_bounds(self, bounds: tuple[int, int, int, int]) -> None:
        self.log.append(("panel_bounds", bounds))

    def set_panel_origin(self, origin: tuple[int, ...]) -> None:
        self.log.append(("panel_origin", origin))

    def set_window_monitor(self, monitor: int) -> None:
        self.log.append(("window_monitor", monitor))

    def set_window_behavior(self, behavior: int) -> None:
        self.log.append(("window_behavior", behavior))

    def monitors(self) -> list[tuple[int, int, int, int, int]]:
        self.log.append(("monitors",))
        return [(0, 0, 1920, 1080, 32)]


class PromptRecorder(LiveIO):
    """A ``LiveIO`` that records every dialog a function shows, ``(message,
    buttons, title)``, and answers each with the next of ``answers`` (the first
    button, 0, once they run out)."""

    def __init__(self, *answers: int) -> None:
        self.answers = list(answers)
        self.dialogs: list[tuple[str, list[str], str]] = []

    def prompt(self, message: str, buttons: Any = ("OK",), title: str = "") -> int:
        self.dialogs.append((message, list(buttons), title))
        return self.answers.pop(0) if self.answers else 0
