"""The module a generated package holds for the front panel every VI has.

A LabVIEW VI owns a front panel: it holds the value of every control and
indicator, a diagram reaches it through a VI reference (the Ctrl Val methods, Run
VI, Property Nodes), and the VI runs with it shown or not. A Python function holds
no value between calls, so each generated function carries a ``Panel`` as
``function.panel``, set right after its ``def`` (after its class, for a method).
The class is written once into the package root as ``panels.py`` and imported from
there, so a generated package imports nothing from lvkit.

A ``Panel`` is a ``PanelValues`` (the names, labels and values of the controls and
indicators, and the views told of every change) that also runs the function. A
Global VI has no function: its ``panel`` is a ``PanelValues`` whose values are the
module's own names (``codegen/global_module.py``), and Run VI on it is error 1035.

A ``Panel`` is described by the one name table of the VI
(``codegen/panel_names.py``: a name unique among every control and indicator) and
the label LabVIEW shows for each (``codegen/panel_description.py``)::

    my_vi.panel = Panel(
        my_vi,
        controls={"x": "x", "list_": "list"},
        indicators={"y": "y", "thing_out": "Thing out"},
        results={"y": "y"},
        passed={"thing_out": "self"},
        defaults={"list_": lambda: []},
        clusters={"cfg": (("a", "a"), ("b", 1), ("inner", "inner", (("x", 0),)))},
        order=["list_", "thing_out", "x", "y"],
    )

``controls`` maps the name of each control to its label; a control whose name is a
parameter of the function is passed to it by a run, any other (a control off the
connector pane) is held only; ``defaults`` a name to the function that makes the
control's default (a control with none takes the parameter's own); ``indicators``
the name of each indicator to its label; ``results`` the name of an indicator on
the connector pane to the field of the result that holds it; ``passed`` the name
of a class output that is the object the method ran on to the parameter
(``self``) that holds it; ``clusters`` the name of a cluster control or indicator
to the specs of its fields in field order, each ``(label, key)`` (``key`` the
position in a cluster that is a tuple, else the attribute name) and, for a field
that is a cluster, ``(label, key, specs)`` (``codegen/panel_clusters.py``);
``order`` is left out when the controls and then the
indicators are in tabbing order.

``Panel.controls`` is the Controls[] property of the panel: a ``Control`` for each
control and indicator, the same object each time. A ``Control`` is what LabVIEW's
reference to the object reads: its ``label``, whether it is an ``indicator``, and
its ``value`` (read and written through the panel, as Ctrl Val does). The
``controls`` of a cluster's ``Control`` are a ``Field`` for each field of the
cluster, in field order: the field's label, the cluster's indicator flag, and the
field of the cluster's value as ``value``.

The panel holds every value by name, so two controls with the same label (or a
control and an indicator) each keep their own. ``order`` lists every name in the
front panel's tabbing order (the order of the heap's ``ddoList``; without it, the
controls and then the indicators), which is the order of ``Panel.controls`` ("the
references in tabbing order") and the order a Ctrl Val method looks a label up in:
NI picks "the first object whose label matches" in the front panel tabbing order,
so a label two objects share is the first one's.

A view of the panel (the NiceGUI tab, a test recorder, a remote mirror) is a
``PanelView`` attached with ``Panel.attach``: the panel tells each attached view,
by name, when a control or an indicator changes, when a control property is
written and when a run starts and ends. With no view attached the panel runs
headless.

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
PANEL_VALUES_NAME = "PanelValues"
PANEL_ERROR_NAME = "PanelError"
PANEL_OF_NAME = "panel_of"
AS_PANEL_NAME = "as_panel"
AS_CONTROL_NAME = "as_control"
# What a node that calls a method of a panel imports where it runs.
PANEL_CALL_IMPORTS = f"{PANEL_OF_NAME}, {PANEL_ERROR_NAME}"

# Run VI on a Global VI raises error 1035 in ``PanelValues.run``: NI, General
# LabVIEW Error Codes (errors/general-labview-error-codes.html), 1035 "Operation is
# invalid for this type of VI."; the Run VI page (properties-and-methods/vi-server/
# vi/run.html) lists "Available with global VIs: No".

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
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from queue import Empty, Queue
from typing import Any

from .errors import LabVIEWError
from .live_io import LiveIO

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


class Control:
    """A control or indicator of a panel, as a reference to it: its ``label``, whether
    it is an ``indicator`` and its ``value``, which is the panel's value of it. A
    cluster's ``controls`` are a reference to each of its fields, in field order."""

    def __init__(
        self,
        panel: PanelValues,
        name: str,
        label: str,
        indicator: bool,
        fields: Sequence[Any] | None = None,
    ) -> None:
        self.panel = panel
        self.name = name
        self.label = label
        self.indicator = indicator
        self._fields = (
            None
            if fields is None
            else [Field(self, label, key, *rest) for label, key, *rest in fields]
        )

    @property
    def value(self) -> Any:
        return self.panel.value(self.name)

    @value.setter
    def value(self, value: Any) -> None:
        self.panel.put(self.name, value)

    @property
    def controls(self) -> list[Control]:
        """The Controls[] of a cluster: a reference to each of its fields, in
        cluster order, the same object each time; error 1026 for a control that is
        not a cluster."""
        if self._fields is None:
            raise PanelError(
                code=1026,
                source="Control.controls",
                message="Control Reference is invalid.",
            )
        return list(self._fields)


class Field(Control):
    """A field of the cluster control ``parent``: the control with the label of the
    field and the indicator flag of the cluster, whose ``value`` is that field of
    the cluster's value. ``key`` is the position of the field in a cluster that is
    a tuple, else the name of its attribute. A write replaces the cluster's value
    with a copy that holds the new field, so a value read earlier is not changed."""

    def __init__(
        self,
        parent: Control,
        label: str,
        key: int | str,
        fields: Sequence[Any] | None = None,
    ) -> None:
        super().__init__(parent.panel, parent.name, label, parent.indicator, fields)
        self.parent = parent
        self.key = key

    @property
    def value(self) -> Any:
        whole = self.parent.value
        if whole is None:
            # a cluster indicator before its first run, or a cluster whose
            # default is None (an error cluster, a class not loaded)
            return None
        if isinstance(self.key, int):
            return whole[self.key]
        return getattr(whole, self.key)

    @value.setter
    def value(self, value: Any) -> None:
        whole = self.parent.value
        if whole is None:
            raise PanelError(
                code=1,
                source="Control.value",
                message=f'"{self.parent.label}" has no value to change a field of',
            )
        if isinstance(self.key, int):
            changed = (*whole[: self.key], value, *whole[self.key + 1 :])
        else:
            changed = copy.copy(whole)
            setattr(changed, self.key, value)
        self.parent.value = changed


class PanelView:
    """What the panel tells a view attached to it, each a no-op to override. The
    panel tells from the thread that made the change, outside its own lock; a
    view that draws records the change and draws on its own thread."""

    def control_changed(self, name: str, value: Any) -> None:
        """The control (or indicator) ``name`` of the panel was set."""

    def indicator_changed(self, name: str, value: Any) -> None:
        """A run stored the value of the indicator ``name``."""

    def property_changed(self, name: str, prop: str, value: Any) -> None:
        """The running VI set the property ``prop`` (the name of the property) of
        the control or indicator ``name``."""

    def run_started(self) -> None:
        """A run of the VI began."""

    def run_finished(self, error: BaseException | None) -> None:
        """A run of the VI ended, with the error it raised, else None."""


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


class PanelIO(LiveIO):
    """The ``live_io`` of one run: what the VI does with its own panel while it
    runs goes to the ``Panel`` and its views. ``events`` is the run's queue. It
    overrides the methods of ``LiveIO`` it forwards to the panel; every other
    method is the base class's."""

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


class PanelValues:
    """The values of a front panel: each control and indicator by name, with the
    label LabVIEW shows for it, and the views told of every change. A Global VI's
    panel is this class over the module's own names (``storage``): its controls
    are the module-level names every global variable node reads and writes, so
    there is one store. ``Panel`` adds the run of a VI's function. Values are
    created on first use, at the controls' defaults."""

    def __init__(
        self,
        controls: Mapping[str, str] | None = None,
        indicators: Mapping[str, str] | None = None,
        defaults: Mapping[str, Callable[[], Any]] | None = None,
        storage: MutableMapping[str, Any] | None = None,
        order: Sequence[str] | None = None,
        clusters: Mapping[str, Sequence[Any]] | None = None,
    ) -> None:
        clusters = clusters or {}
        self._controls = dict(controls or {})
        self._indicators = dict(indicators or {})
        self._defaults = dict(defaults or {})
        self._order = list(order or [*self._controls, *self._indicators])
        self._refs = {
            name: Control(
                self, name, label, name in self._indicators, clusters.get(name)
            )
            for name, label in {**self._controls, **self._indicators}.items()
        }
        self._values: MutableMapping[str, Any] = {} if storage is None else storage
        self._views: list[PanelView] = []
        self._lock = threading.RLock()

    def attach(self, view: PanelView) -> None:
        """Tell ``view`` of every change from now on."""
        with self._lock:
            if view not in self._views:
                self._views.append(view)

    def detach(self, view: PanelView) -> None:
        with self._lock:
            if view in self._views:
                self._views.remove(view)

    def set(self, label: str, value: Any) -> None:
        """Set the control or indicator labelled ``label``."""
        self.put(self._name_of(label), value)

    def get(self, label: str) -> Any:
        """The value of the control or indicator labelled ``label``: the last set
        or run's, else the control's default."""
        return self.value(self._name_of(label))

    def get_all(self, controls: bool = False) -> list[tuple[str, Any]]:
        """``(label, value)`` of every labelled control (``controls`` True) or
        indicator in tabbing order, a label two share once for each."""
        with self._lock:
            names = self._controls if controls else self._indicators
            return [
                (names[name], self._value(name))
                for name in self._order
                if names.get(name)
            ]

    @property
    def controls(self) -> list[Control]:
        """The Controls[] of the panel: a reference to each control and indicator,
        in tabbing order."""
        return [self._refs[name] for name in self._order]

    def holds(self, name: str) -> bool:
        """Whether ``name`` is the name of a control or indicator of the panel."""
        return name in self._controls or name in self._indicators

    def put(self, name: str, value: Any) -> None:
        """Set the control or indicator ``name`` and tell the views."""
        self._check(name)
        with self._lock:
            self._values[name] = value
        self._tell("control_changed", name, value)

    def value(self, name: str) -> Any:
        """The value of the control or indicator ``name``."""
        self._check(name)
        with self._lock:
            return self._value(name)

    def seed(self, name: str, value: Any) -> Any:
        """The value of ``name``, which is ``value`` when none was set yet (a view
        starts a control at the value its widget has). No view is told."""
        self._check(name)
        with self._lock:
            if name not in self._values:
                self._values[name] = value
            return self._values[name]

    def run(self, wait: bool = True) -> None:
        """Run VI on a panel that is not a VI's function: a Global VI has no
        diagram to run. Error 1035: NI, General LabVIEW Error Codes
        (errors/general-labview-error-codes.html), "Operation is invalid for this
        type of VI."; NI's Run VI page lists "Available with global VIs: No"
        (properties-and-methods/vi-server/vi/run.html)."""
        raise PanelError(
            code=1035,
            source="Panel.run",
            message="Operation is invalid for this type of VI.",
        )

    @property
    def state(self) -> int:
        """Exec.State of a panel that is not a VI's function: a Global VI never
        runs, so it is error 1035, as Run VI on it is."""
        raise PanelError(
            code=1035,
            source="Panel.state",
            message="Operation is invalid for this type of VI.",
        )

    def _tell(self, method: str, *arguments: Any) -> None:
        with self._lock:
            views = list(self._views)
        for view in views:
            getattr(view, method)(*arguments)

    def _name_of(self, label: str) -> str:
        """The name of the first control or indicator labelled ``label`` in
        tabbing order; error 1 for a label none has."""
        for name in self._order:
            if self._refs[name].label == label:
                return name
        raise PanelError(code=1, source="Panel", message=f'"{label}" not found')

    def _check(self, name: str) -> None:
        if not self.holds(name):
            raise PanelError(code=1, source="Panel", message=f'"{name}" not found')

    def _value(self, name: str) -> Any:
        """The panel's value of ``name``. A control with a default made for it
        holds that from the first read on, as a front panel holds one value; else
        the control's own default (``_own_default``), else None."""
        if name in self._values:
            return self._values[name]
        if name in self._controls:
            factory = self._defaults.get(name)
            if factory is not None:
                self._values[name] = factory()
                return self._values[name]
            return self._own_default(name)
        return None

    def _own_default(self, name: str) -> Any:
        """The default of the control ``name`` when no function makes one: None."""
        return None


class Panel(PanelValues):
    """The front panel of one VI's function: the values (``PanelValues``) and
    whether the VI is running."""

    def __init__(
        self,
        function: Callable[..., Any],
        controls: Mapping[str, str] | None = None,
        indicators: Mapping[str, str] | None = None,
        results: Mapping[str, str] | None = None,
        passed: Mapping[str, str] | None = None,
        defaults: Mapping[str, Callable[[], Any]] | None = None,
        order: Sequence[str] | None = None,
        clusters: Mapping[str, Sequence[Any]] | None = None,
    ) -> None:
        super().__init__(
            controls, indicators, defaults, order=order, clusters=clusters
        )
        self.function = function
        self._results = dict(results or {})
        self._passed = dict(passed or {})
        self._running = False
        self._abort = False
        self._go = threading.Event()
        self._go.set()
        self._io: PanelIO | None = None

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

    def run(self, wait: bool = True) -> None:
        """Run the function with the control values the panel holds, an unset
        control at its default. The panel is Running (``state`` 2) when this
        returns, whether or not it waits: ``wait`` False starts the work on a
        daemon thread after the run is recorded, so it runs to its end on its own
        and does not keep the program from exiting. A run while the VI is running
        is an error."""
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
                _start_thread(self._execute)
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

    def _live_read(self, name: str, held: Any) -> Any:
        """The panel's value of the control ``name``, a copy the function may
        change; ``held`` (the function's own) when the panel has none."""
        with self._lock:
            if name not in self._values:
                return copy.deepcopy(held)
            return copy.deepcopy(self._values[name])

    def _live_write(self, name: str, value: Any) -> None:
        """The function wrote the indicator, or the control through a Local
        Variable, ``name`` stands for."""
        if not self.holds(name):
            return
        with self._lock:
            self._values[name] = value
        changed = "control_changed" if name in self._controls else "indicator_changed"
        self._tell(changed, name, value)

    def _own_default(self, name: str) -> Any:
        """The default of the control ``name`` when no function makes one: its
        parameter's own default, else None."""
        parameter = inspect.signature(self.function).parameters.get(name)
        if parameter is not None and parameter.default is not parameter.empty:
            return parameter.default
        return None

    def _live_property(self, name: str, prop: str, value: Any) -> None:
        if self.holds(name):
            self._tell("property_changed", name, prop, value)

    def _arguments(self) -> dict[str, Any]:
        """The keyword arguments of a run: each control that is a parameter of the
        function and was set or has a default made for it, and a parameter that
        has no default at None."""
        parameters = inspect.signature(self.function).parameters
        with self._lock:
            arguments = {
                name: self._value(name)
                for name in self._controls
                if name in parameters
                and (name in self._values or name in self._defaults)
            }
        for name, found in parameters.items():
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
                for name, field in self._results.items():
                    self._values[name] = getattr(result, field, None)
                    stored.append((name, self._values[name]))
            for name, parameter in self._passed.items():
                self._values[name] = arguments[parameter]
                stored.append((name, self._values[name]))
        for name, value in stored:
            self._tell("indicator_changed", name, value)


def panel_of(reference: Any) -> PanelValues:
    """The panel of the VI ``reference`` holds: its function's ``panel`` (a Global
    VI's reference is its module, whose ``panel`` holds its controls); error 1026
    for a reference that holds no VI (not a refnum, or an object with no
    panel)."""
    panel = getattr(reference, "panel", None)
    if not isinstance(panel, PanelValues):
        raise PanelError(
            code=1026, source="panel_of", message="VI Reference is invalid."
        )
    return panel


def as_panel(reference: Any) -> PanelValues:
    """``reference`` as the reference to a front panel (the ``Panel`` property of a
    VI gives one); error 1026 for a reference that holds none."""
    if not isinstance(reference, PanelValues):
        raise PanelError(
            code=1026, source="as_panel", message="VI Reference is invalid."
        )
    return reference


def as_control(reference: Any) -> Control:
    """``reference`` as the reference to a control or indicator (an element of
    ``Panel.controls``); error 1026 for a reference that holds none."""
    if not isinstance(reference, Control):
        raise PanelError(
            code=1026, source="as_control", message="Control Reference is invalid."
        )
    return reference
'''
