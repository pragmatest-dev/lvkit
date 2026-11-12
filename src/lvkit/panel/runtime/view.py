"""The NiceGUI view of a VI's front panel: the widgets of the page, kept in step
with the ``Panel`` of the VI's function (the generated ``panels.py``).

The panel owns every value. This view draws them: a control the user changes is
set on the panel; a control or indicator changed on the panel (by a Ctrl Val
method from another VI, by a run that stored its indicators, by the running VI
writing one) is drawn in its widget.

The panel tells its views from the thread that made the change, which is a worker
thread when a VI runs. Only NiceGUI's loop touches a widget: each notification is
recorded under the view's own lock, and one timer on the loop calls ``paint``,
which draws the latest value of each changed name and then hands the run's
start and end to the toolbar.

Everything is by name: the name a widget is built with is the name of its control
or indicator on the panel (``codegen/panel_names.py``), which also tells the view
of a change by name.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .events import PanelEvents

if TYPE_CHECKING:
    from .port import Port

# What ``paint`` tells the toolbar: "started" or "finished" with the error the run
# raised (else None).
RunListener = Callable[[str, BaseException | None], None]


def key_of(name: Any) -> str:
    """The string a ``live_io`` name stands for: the value of the enum member the
    function passes, or the string itself."""
    return str(getattr(name, "value", name))


class PanelWidgets:
    """The widgets of one panel page. ``panel`` is the ``Panel`` of the VI's
    function (None draws the widgets with nothing behind them) and
    ``kinds`` is the event enum of the VI's module when the VI holds an Event
    Structure (the view then pushes the user's changes into the run's events)."""

    def __init__(
        self,
        panel: Any = None,
        kinds: Any = None,
    ) -> None:
        self.panel = panel
        self.panel_events = (
            PanelEvents(panel, kinds)
            if panel is not None and kinds is not None
            else None
        )
        self._lock = threading.Lock()
        self._latest: dict[str, Any] = {}
        self._dirty: dict[str, None] = {}
        self._steps: list[tuple[str, BaseException | None]] = []
        self._painters: dict[str, Callable[[Any], None]] = {}
        self._shown: dict[str, Any] = {}
        self._painting = False
        self._own = threading.local()
        self._run_listener: RunListener | None = None

    # -- the widgets (on the event loop) -----------------------------------

    def add_control(self, name: Any, port: Port) -> None:
        """A control: it starts at the panel's value (the widget's own when the
        panel holds none yet) and the user's changes are set on the panel."""
        key = key_of(name)
        starting = port.get()
        if self.panel is not None and self.panel.holds(key):
            held = self.panel.seed(key, copy.deepcopy(starting))
            if held != starting:
                self._show(port.show, held)
        self._shown[key] = port.get()
        self._painters[key] = port.show
        port.on_change(lambda value: self.set_control(name, value))

    def add_indicator(self, name: Any, painter: Callable[[Any], None]) -> None:
        """An indicator, with the function that shows a value in its widget. It
        shows the panel's value when the panel holds one."""
        key = key_of(name)
        self._painters[key] = painter
        if self.panel is not None and self.panel.holds(key):
            held = self.panel.value(key)
            if held is not None:
                self._show(painter, held)

    def set_control(self, name: Any, value: Any) -> None:
        """A control the user changed (called from its change handler): set it on
        the panel, and push a Value Change into the run's events."""
        if self._painting:
            return
        key = key_of(name)
        old = self._shown.get(key)
        self._shown[key] = value
        if self.panel is not None and self.panel.holds(key):
            self._own.setting = True
            try:
                self.panel.put(key, copy.deepcopy(value))
            finally:
                self._own.setting = False
        if self.panel_events is not None:
            self.panel_events.value_changed(key, value, old)

    def _show(self, painter: Callable[[Any], None], value: Any) -> None:
        """Draw ``value`` in a widget without that counting as the user's change."""
        self._painting = True
        try:
            painter(value)
        finally:
            self._painting = False

    def paint(self) -> None:
        """Draw the latest value of every name that changed since the last call,
        then tell the toolbar of the runs that started or ended. Called by the
        timer, on the event loop."""
        with self._lock:
            changed = [(k, self._latest[k]) for k in self._dirty]
            self._dirty.clear()
            steps = self._steps
            self._steps = []
        for name, value in changed:
            painter = self._painters.get(name)
            if painter is not None:
                self._shown[name] = value
                self._show(painter, value)
        if self._run_listener is not None:
            for kind, error in steps:
                self._run_listener(kind, error)

    def on_run(self, listener: RunListener) -> None:
        """Tell ``listener`` (the toolbar) of each run's start and end, from
        ``paint``."""
        self._run_listener = listener

    # -- what the panel tells (on any thread) ------------------------------

    def control_changed(self, name: str, value: Any) -> None:
        self._record(name, value)

    def indicator_changed(self, name: str, value: Any) -> None:
        self._record(name, value)

    def property_changed(self, name: str, prop: str, value: Any) -> None:
        """The page draws no control property (Visible, Disabled, ...) yet."""

    def run_started(self) -> None:
        with self._lock:
            self._steps.append(("started", None))

    def run_finished(self, error: BaseException | None) -> None:
        with self._lock:
            self._steps.append(("finished", error))

    def _record(self, name: str, value: Any) -> None:
        """A change on the panel. A change this view set itself is already in its
        widget."""
        if getattr(self._own, "setting", False):
            return
        with self._lock:
            self._latest[name] = value
            self._dirty[name] = None
