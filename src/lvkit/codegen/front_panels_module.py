"""The module a generated package holds for a VI's front panel by reference.

A VI's front panel holds the value of every control and indicator between calls:
a diagram sets controls through a VI reference (the Ctrl Val.Set method), runs
the VI (the Run VI method) and reads indicators back (Ctrl Val.Get and Get All),
in one VI or across several. A Python function holds no value between calls, so
the package gets a small ``FrontPanel`` class, one per function, that does. It
is written once into the package root as ``front_panels.py``, only when a
generated module imports it, and imported from there, so a generated package
imports nothing from lvkit.

Each generated function carries two function attributes, set after its ``def``
(after its class, for a method): ``panel_controls`` maps the label of each
connector-pane control to the function's parameter, and ``panel_indicators`` the
label of each indicator to the field of the result; ``panel_passed`` maps the
label of a class output that is the object the method ran on to its parameter.
"""

from __future__ import annotations

FRONT_PANELS_MODULE_NAME = "front_panels"
FRONT_PANEL_NAME = "FrontPanel"
FRONT_PANEL_ERROR_NAME = "FrontPanelError"

FRONT_PANELS_MODULE_SOURCE = '''"""A VI's front panel, by reference."""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .errors import LabVIEWError

_pool = ThreadPoolExecutor()
_panels: dict[Callable[..., Any], FrontPanel] = {}
_panels_lock = threading.Lock()


class FrontPanelError(LabVIEWError):
    """The error a front panel method gives: an invalid VI reference, or a
    control name the VI does not have."""


class FrontPanel:
    """The front panel of one VI's function: the value of each control and
    indicator between calls. ``FrontPanel.of(function)`` is the same panel for
    every caller, so a value set in one function is the value another runs or
    reads."""

    def __init__(self, function: Callable[..., Any]) -> None:
        self._function = function
        self._controls: dict[str, str] = dict(
            getattr(function, "panel_controls", {})
        )
        self._indicators: dict[str, str] = dict(
            getattr(function, "panel_indicators", {})
        )
        self._passed: dict[str, str] = dict(getattr(function, "panel_passed", {}))
        self._parameters = inspect.signature(function).parameters
        self._values: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._running = 0

    @classmethod
    def of(cls, function: Callable[..., Any] | None) -> FrontPanel:
        """The front panel of ``function``; error 1026 for a reference that is
        not a VI."""
        if function is None:
            raise FrontPanelError(
                code=1026, source="FrontPanel", message="VI Reference is invalid."
            )
        with _panels_lock:
            panel = _panels.get(function)
            if panel is None:
                panel = _panels[function] = cls(function)
            return panel

    @property
    def state(self) -> int:
        """Execution state: 1 (Idle) while nothing runs, 2 (Run top level) while
        a run is in progress."""
        with self._lock:
            return 2 if self._running else 1

    def set(self, label: str, value: Any) -> None:
        """Set the control or indicator labelled ``label``."""
        self._check(label)
        with self._lock:
            self._values[label] = value

    def get(self, label: str) -> Any:
        """The value of the control or indicator labelled ``label``: the last
        set or run's, else the control's default."""
        self._check(label)
        with self._lock:
            return self._value(label)

    def get_all(self, controls: bool = False) -> list[tuple[str, Any]]:
        """``(label, value)`` of every control (``controls`` True) or indicator."""
        with self._lock:
            labels = self._controls if controls else self._indicator_labels()
            return [(label, self._value(label)) for label in labels]

    def run(self, wait: bool = True) -> None:
        """Run the function with the control values set, an unset control at its
        default. ``wait`` False starts it on a thread pool and returns."""
        with self._lock:
            self._running += 1
        if wait:
            self._finish_run()
        else:
            _pool.submit(self._finish_run)

    def _finish_run(self) -> None:
        try:
            self._execute()
        finally:
            with self._lock:
                self._running -= 1

    def _execute(self) -> None:
        with self._lock:
            arguments = {
                parameter: self._values[label]
                for label, parameter in self._controls.items()
                if label in self._values
            }
        for parameter, found in self._parameters.items():
            if (
                parameter not in arguments
                and found.default is inspect.Parameter.empty
                and found.kind in (found.POSITIONAL_OR_KEYWORD, found.KEYWORD_ONLY)
            ):
                arguments[parameter] = None
        result = self._function(**arguments)
        with self._lock:
            if result is not None:
                for label, field in self._indicators.items():
                    self._values[label] = getattr(result, field, None)
            for label, parameter in self._passed.items():
                self._values[label] = arguments[parameter]

    def _check(self, label: str) -> None:
        if label not in self._controls and label not in self._indicator_labels():
            raise FrontPanelError(
                code=1,
                source="FrontPanel",
                message=f'"{label}" not found',
            )

    def _indicator_labels(self) -> list[str]:
        return [*self._indicators, *self._passed]

    def _value(self, label: str) -> Any:
        if label in self._values:
            return self._values[label]
        parameter = self._parameters.get(self._controls.get(label, ""))
        if parameter is None or parameter.default is inspect.Parameter.empty:
            return None
        return parameter.default
'''
