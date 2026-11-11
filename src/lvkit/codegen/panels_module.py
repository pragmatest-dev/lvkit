"""The module a generated package holds for the front panel every VI has.

A LabVIEW VI owns a front panel: it holds the value of every control and
indicator, a diagram reaches it through a VI reference (the Ctrl Val methods, Run
VI, Property Nodes), and the VI runs with it shown or not. A Python function holds
no value between calls, so each generated function carries a ``Panel`` as
``function.panel``, set right after its ``def`` (after its class, for a method).
The class is written once into the package root as ``panels.py`` and imported from
there, so a generated package imports nothing from lvkit.

A ``Panel`` is described by the labels LabVIEW shows
(``codegen/panel_description.py``)::

    my_vi.panel = Panel(
        my_vi,
        controls={"x": "x", "list": "list_"},
        indicators={"y": "y"},
        passed={"Thing out": "self"},
        duplicates=["x"],
        defaults={"list": lambda: []},
    )

``controls`` maps a label to the parameter that takes it (None for a control
that is not on the connector pane); ``defaults`` a label to the function that
makes the control's default (a control with none takes the parameter's own);
``indicators`` a label to the field of
the result that holds it (None for an indicator not on the connector pane);
``passed`` the label of a class output that is the object the method ran on to the
parameter (``self``) that holds it; ``duplicates`` the labels two objects share,
which a Ctrl Val method cannot tell apart.

A view of the panel (the NiceGUI tab, a test recorder, a remote mirror) is a
``PanelView`` attached with ``Panel.attach``: the panel tells each attached view
when a control or an indicator changes, when a control property is written and when
a run starts and ends. With no view attached the panel runs headless.

A run passes a function that takes ``live_io`` (the VI reads or writes its own
panel while it runs) a ``PanelIO``: its reads give the panel's control values, its
writes store the indicator on the panel and tell the views, and every call is a
point where ``Panel.abort`` ends the run and ``Panel.pause`` holds it. The
function's Event Structure reads ``Panel.events``, the queue of the run, which a
view pushes its events into.
"""

from __future__ import annotations

PANELS_MODULE_NAME = "panels"
PANEL_NAME = "Panel"
PANEL_ERROR_NAME = "PanelError"
PANEL_OF_NAME = "panel_of"
# What a node that calls a method of a panel imports where it runs.
PANEL_CALL_IMPORTS = f"{PANEL_OF_NAME}, {PANEL_ERROR_NAME}"

# A second Run VI on a VI that is running raises error 1000 in ``Panel.run``: NI,
# General LabVIEW Error Codes (errors/general-labview-error-codes.html), 1000 "The
# VI is not in a state compatible with this operation. … This error also can occur
# if you try to run a VI using the run method while the target VI is running or
# reserved for running."

PANELS_MODULE_SOURCE = '''"""The front panel of a VI."""

from __future__ import annotations

import copy
import inspect
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from queue import Empty, Queue
from typing import Any

from .errors import LabVIEWError

ABORT_POLL = 0.1


class PanelError(LabVIEWError):
    """The error a front panel method gives: an invalid VI reference, a control
    name the VI does not have, or a VI that is already running."""


class Abort(BaseException):
    """Raised from the points of a run (``PanelIO`` and the run's events queue)
    once ``Panel.abort`` was called, so the running function ends there. A
    ``BaseException`` so no ``except Exception`` in the function swallows it."""


def key_of(name: Any) -> str:
    """The string a ``live_io`` name stands for: the value of the enum member the
    function passes, or the string itself."""
    return str(getattr(name, "value", name))


class PanelView:
    """What the panel tells a view attached to it, each a no-op to override. The
    panel tells from the thread that made the change, outside its own lock; a
    view that draws records the change and draws on its own thread."""

    def control_changed(self, label: str, value: Any) -> None:
        """A control (or indicator) of the panel was set."""

    def indicator_changed(self, label: str, value: Any) -> None:
        """A run stored the value of an indicator."""

    def property_changed(self, label: str, prop: str, value: Any) -> None:
        """The running VI set the property ``prop`` (the name of the property) of
        the control or indicator labelled ``label``."""

    def run_started(self) -> None:
        """A run of the VI began."""

    def run_finished(self, error: BaseException | None) -> None:
        """A run of the VI ended, with the error it raised, else None."""


# A control as the panel holds it: the parameter that takes it (None off the
# connector pane) and the function that makes its default (None for the
# parameter's own).
ControlEntry = tuple[str | None, Callable[[], Any] | None]


def _start_thread(work: Callable[[], Any]) -> None:
    threading.Thread(target=work, daemon=True).start()


class _Inbox(Queue):
    """The queue of a run's events. A wait for the next event is a point of the
    run too: it ends when the run is aborted and holds while it is paused, so a VI
    blocked in its Event Structure can be aborted; the wait's own timeout is
    kept."""

    def __init__(self, checkpoint: Callable[[], None]) -> None:
        super().__init__()
        self._checkpoint = checkpoint

    def get(self, block: bool = True, timeout: float | None = None) -> Any:
        if not block:
            return super().get(block=False)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._checkpoint()
            step = ABORT_POLL
            if deadline is not None:
                step = min(step, max(0.0, deadline - time.monotonic()))
            try:
                return super().get(timeout=step)
            except Empty:
                if deadline is not None and time.monotonic() >= deadline:
                    raise


class PanelIO:
    """The ``live_io`` of one run: what the VI does with its own panel while it
    runs goes to the ``Panel`` and its views. ``events`` is the run's queue. It has
    the methods of ``LiveIO`` without importing it, so a package whose VIs take no
    ``live_io`` has no ``live_io.py``."""

    def __init__(self, panel: Panel, events: Queue[Any]) -> None:
        self._panel = panel
        self.events = events

    def read(self, name: Any, value: Any) -> Any:
        """The panel's value of the control ``name``, else ``value``."""
        self._panel._checkpoint()
        return self._panel._live_read(key_of(name), value)

    def write(self, name: Any, value: Any) -> None:
        self._panel._checkpoint()
        self._panel._live_write(key_of(name), copy.copy(value))

    def set_property(self, name: Any, prop: Any, value: Any) -> Any:
        self._panel._checkpoint()
        self._panel._live_property(key_of(name), key_of(prop), value)


class Panel:
    """The front panel of one VI's function: the value of each control and
    indicator between calls, and whether the VI is running. Values are created on
    first use, at the controls' defaults."""

    def __init__(
        self,
        function: Callable[..., Any],
        controls: Mapping[str, str | None] | None = None,
        indicators: Mapping[str, str | None] | None = None,
        passed: Mapping[str, str] | None = None,
        duplicates: Iterable[str] = (),
        defaults: Mapping[str, Callable[[], Any]] | None = None,
    ) -> None:
        self.function = function
        made = defaults or {}
        self._controls: dict[str, ControlEntry] = {
            label: (name, made.get(label)) for label, name in (controls or {}).items()
        }
        self._indicators = dict(indicators or {})
        self._passed = dict(passed or {})
        self._duplicates = frozenset(duplicates)
        self._values: dict[str, Any] = {}
        self._views: list[PanelView] = []
        self._lock = threading.RLock()
        self._running = False
        self._abort = False
        self._go = threading.Event()
        self._go.set()
        self._io: PanelIO | None = None
        # The label of each name a running function passes ``live_io``: the
        # parameter of a control, the field of an indicator (a view adds the
        # names it knows).
        self._names: dict[str, str] = {
            **{f: label for label, f in self._indicators.items() if f is not None},
            **{n: label for label, (n, _) in self._controls.items() if n is not None},
        }

    @property
    def state(self) -> int:
        """Execution state: 1 (Idle) while nothing runs, 2 (Run top level) while
        a run is in progress."""
        with self._lock:
            return 2 if self._running else 1

    @property
    def paused(self) -> bool:
        return not self._go.is_set()

    @property
    def aborted(self) -> bool:
        """Whether the last run was aborted (cleared when the next run starts)."""
        return self._abort

    @property
    def events(self) -> Queue[Any] | None:
        """The events queue of the run in progress, None when nothing runs or the
        function takes no ``live_io``."""
        with self._lock:
            return self._io.events if self._io is not None else None

    def attach(self, view: PanelView, names: Mapping[str, str] | None = None) -> None:
        """Tell ``view`` of every change from now on. ``names`` maps the names a
        running function passes ``live_io`` to the labels of the controls and
        indicators the view draws."""
        with self._lock:
            if view not in self._views:
                self._views.append(view)
            self._names.update(names or {})

    def detach(self, view: PanelView) -> None:
        with self._lock:
            if view in self._views:
                self._views.remove(view)

    def set(self, label: str, value: Any) -> None:
        """Set the control or indicator labelled ``label``."""
        self._check(label)
        with self._lock:
            self._values[label] = value
        self._tell("control_changed", label, value)

    def has(self, label: str) -> bool:
        """Whether ``label`` names exactly one control or indicator."""
        return label not in self._duplicates and (
            label in self._controls or label in self._indicator_labels()
        )

    def seed(self, label: str, value: Any) -> Any:
        """The value of ``label``, which is ``value`` when none was set yet (a
        view starts a control at the value its widget has). No view is told."""
        self._check(label)
        with self._lock:
            return self._values.setdefault(label, value)

    def get(self, label: str) -> Any:
        """The value of the control or indicator labelled ``label``: the last set
        or run's, else the control's default."""
        self._check(label)
        with self._lock:
            return self._value(label)

    def get_all(self, controls: bool = False) -> list[tuple[str, Any]]:
        """``(label, value)`` of every control (``controls`` True) or indicator."""
        with self._lock:
            labels = self._controls if controls else self._indicator_labels()
            return [(label, self._value(label)) for label in labels]

    def run(
        self,
        wait: bool = True,
        submit: Callable[[Callable[[], Any]], Any] | None = None,
    ) -> None:
        """Run the function with the control values the panel holds, an unset
        control at its default. The panel is Running (``state`` 2) when this
        returns, whether or not it waits: ``wait`` False hands the work to
        ``submit`` (the caller's thread pool; a new thread when None) after the
        run is recorded. A run while the VI is running is an error."""
        with self._lock:
            if self._running:
                raise PanelError(
                    code=1000,
                    source="Panel.run",
                    message="The VI is not in a state compatible with this operation.",
                )
            self._running = True
            self._abort = False
            self._go.set()
            if "live_io" in inspect.signature(self.function).parameters:
                self._io = PanelIO(self, _Inbox(self._checkpoint))
        self._tell("run_started")
        if wait:
            self._execute()
        else:
            try:
                (submit or _start_thread)(self._execute)
            except BaseException as raised:
                self._finish(raised)
                raise

    def _execute(self) -> None:
        error: BaseException | None = None
        try:
            arguments = self._arguments()
            if self._io is not None:
                arguments["live_io"] = self._io
            result = self.function(**arguments)
            self._store(result, arguments)
        except Abort:
            pass
        except BaseException as raised:
            error = raised
            raise
        finally:
            self._finish(error)

    def _finish(self, error: BaseException | None) -> None:
        """The run is over: the VI is idle again and the views are told."""
        with self._lock:
            self._running = False
            self._io = None
            self._go.set()
        self._tell("run_finished", error)

    def abort(self) -> None:
        """End the run in progress at its next point (a ``live_io`` call or a wait
        for an event); a paused run is released so it reaches that point. A VI that
        takes no ``live_io`` has no such point and runs to its end."""
        with self._lock:
            if self._running:
                self._abort = True
                self._go.set()

    def pause(self) -> None:
        """Hold the run in progress at its next point."""
        with self._lock:
            if self._running:
                self._go.clear()

    def resume(self) -> None:
        """Release a run held by ``pause``."""
        self._go.set()

    def _checkpoint(self) -> None:
        """A point of the run: block while paused, then end the run if it was
        aborted."""
        self._go.wait()
        if self._abort:
            raise Abort

    def _live_read(self, key: str, held: Any) -> Any:
        """The panel's value of the control ``key`` names, a copy the function may
        change; ``held`` (the function's own) when the panel has none."""
        label = self._names.get(key)
        with self._lock:
            if label is None or label not in self._values:
                return copy.deepcopy(held)
            return copy.deepcopy(self._values[label])

    def _live_write(self, key: str, value: Any) -> None:
        """The function wrote the indicator, or the control through a Local
        Variable, ``key`` names."""
        label = self._names.get(key)
        if label is None:
            return
        with self._lock:
            self._values[label] = value
            is_control = label in self._controls
        changed = "control_changed" if is_control else "indicator_changed"
        self._tell(changed, label, value)

    def _live_property(self, key: str, prop: str, value: Any) -> None:
        label = self._names.get(key)
        if label is not None:
            self._tell("property_changed", label, prop, value)

    def _arguments(self) -> dict[str, Any]:
        """The keyword arguments of a run: each control of the connector pane that
        was set or has a default made for it, and a parameter that has no default
        at None."""
        with self._lock:
            arguments = {
                name: self._value(label)
                for label, (name, factory) in self._controls.items()
                if name is not None and (label in self._values or factory is not None)
            }
        for name, found in inspect.signature(self.function).parameters.items():
            if (
                name not in arguments
                and found.default is inspect.Parameter.empty
                and found.kind in (found.POSITIONAL_OR_KEYWORD, found.KEYWORD_ONLY)
            ):
                arguments[name] = None
        return arguments

    def _store(self, result: Any, arguments: dict[str, Any]) -> None:
        stored: list[tuple[str, Any]] = []
        with self._lock:
            if result is not None:
                for label, field in self._indicators.items():
                    if field is not None:
                        self._values[label] = getattr(result, field, None)
                        stored.append((label, self._values[label]))
            for label, name in self._passed.items():
                self._values[label] = arguments[name]
                stored.append((label, self._values[label]))
        for label, value in stored:
            self._tell("indicator_changed", label, value)

    def _tell(self, method: str, *arguments: Any) -> None:
        with self._lock:
            views = list(self._views)
        for view in views:
            getattr(view, method)(*arguments)

    def _check(self, label: str) -> None:
        if label in self._duplicates:
            raise PanelError(
                code=1,
                source="Panel",
                message=(
                    f'"{label}" is the label of more than one control or '
                    f"indicator of {self.function.__name__}"
                ),
            )
        if label not in self._controls and label not in self._indicator_labels():
            raise PanelError(code=1, source="Panel", message=f'"{label}" not found')

    def _indicator_labels(self) -> list[str]:
        return [*self._indicators, *self._passed]

    def _value(self, label: str) -> Any:
        """The panel's value of ``label``. A control with a default made for it
        holds that from the first read on, as a front panel holds one value; else
        the parameter's own default, else None."""
        if label in self._values:
            return self._values[label]
        if label in self._controls:
            name, factory = self._controls[label]
            if factory is not None:
                self._values[label] = factory()
                return self._values[label]
            parameter = (
                inspect.signature(self.function).parameters.get(name)
                if name is not None
                else None
            )
            if parameter is not None and parameter.default is not parameter.empty:
                return parameter.default
        return None


def panel_of(reference: Any) -> Panel:
    """The panel of the VI ``reference`` holds: its function's ``panel``; error
    1026 for a reference that holds no VI (not a refnum, or an object with no
    panel)."""
    panel = getattr(reference, "panel", None)
    if not isinstance(panel, Panel):
        raise PanelError(
            code=1026, source="panel_of", message="VI Reference is invalid."
        )
    return panel
'''
