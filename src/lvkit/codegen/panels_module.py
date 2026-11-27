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
        types={"x": LV_DBL, "y": LV_DBL},
        variants=["any"],
        order=["list_", "thing_out", "x", "y"],
        states={"x": {"visible": False, "disabled": 2}},
        classes={"x": "Digital", "y": "Digital"},
    )

``classes`` maps the name of each control and indicator whose front-panel class has
a VI Server class (``control_classes.json``) to that class's name; the ``Control``
of a reference reads it as its ``class_id``, NI's number for the class (``CLASS_IDS``,
NI's published ClassID table), so a VI that tells a control's kind by ClassID takes
the same case LabVIEW does. ``front_panel_classes`` maps the name of each control
with no VI Server class to its front-panel class; reading the ``class_id`` of such
a control raises NotImplementedError naming the control and that class (the
conversion does not know the class; a message built when it is read).

``states`` maps the name of each control or indicator that is stored in a state
other than the default (hidden, disabled or greyed out) to its properties, by the
member name of the property (``control_properties.json``); the panel holds the
state of each property from then on (``Panel.property_of``): a view starts the
widget in it and a running VI's ``get_property`` reads it.

``saved`` maps the name of each control or indicator that has a property the file
saves and a running VI cannot change (NI: "Settable when the VI is running: No", an
Enum's ``Strings[]`` and a Boolean's ``MechAction``) to those properties by member
name, leaving out one that is the default of the control's VI Server class
(``SAVED_DEFAULTS``: a Boolean's Switch When Pressed); ``Control.get_property``
reads them after the state a write or the file's flags set. ``saved`` also holds the
starting value of a settable property the file records in a field of the control's
ddo (a Tip Strip, a graph's scale range, a listbox's items), which a write
replaces.

``elements`` maps the name of each array control or indicator to the element its
Array Element property is a reference to, ``(class, front_panel_class, saved)``
(``codegen/panel_description.py``): ``Control.array_element`` is an ``Element``, a
control of that class (its ``class_id``) holding the properties the element is saved
with, whose own value, label and states the panel does not record.

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
``types`` the name of each control and indicator whose type has a type-descriptor
code to its ``LVType`` (``variants.py``) and ``variants`` the name of each one that
is a Variant (``codegen/panel_types.py``); ``order`` is left out when the controls
and then the indicators are in tabbing order.

A panel holds each control's value in the control's own type, and the Ctrl Val
methods and the Value property of a control reference carry a ``Variant``:
``set_variant`` / ``Control.variant = v`` store a ``Variant`` in a control that holds
a variant and the data of ``v`` in any other (an empty variant has none: error 1),
and ``get_variant`` / ``get_all_variants`` / ``Control.variant`` give a control's
value as ``Variant(value, types[name])`` (a control that holds a variant gives its
own). ``set``, ``get``, ``get_all`` and ``Control.value`` read and write the plain
value, for a Python caller and a view.

``Panel.controls`` is the Controls[] property of the panel: a ``Control`` for each
control and indicator, the same object each time. A ``Control`` is what LabVIEW's
reference to the object reads: its ``label``, whether it is an ``indicator``, and
its ``value`` (read and written through the panel, as Ctrl Val does). The
``controls`` of a cluster's ``Control`` are a ``Field`` for each field of the
cluster, in field order: the field's label, the cluster's indicator flag, and the
field of the cluster's value as ``value``. ``Control.get_property(prop)`` and
``Control.set_property(prop, value)`` read and write a control property the panel
holds (a Combo Box's ``"strings"``): the read is the last write, else the state in
``states``; the write is held and told to the views.

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

Like ``window_title``, ``function.vi_name`` is an attribute the VI's function
carries: its LabVIEW qualified name (``Lib.lvlib:Class.lvclass:Method.vi``).
``call_chain()`` is the Call Chain function: it reads Python's own stack and gives
the ``vi_name`` of every VI function on it, the VI it is called from first. It lives
here because it reports the VIs as run (the function objects that carry ``panel``,
``window_title`` and ``vi_name``), which is what this module's ``panel_of`` reaches.

The module ends with ``application`` and ``loaded_vis()``, the application the VIs
run in and Application:All VIs In Memory (``codegen/panels_application.py``).

The tags of a VI (Tag.Get Tag, Tag.Set Tag and Tag.Remove Tag on its reference) are
a third attribute the VI's function carries, ``function.tags``, a ``Tags``: the tags
the VI's file saves (its ``VITS`` block) and what the program sets. A VI that saves
none has no ``tags`` until the first Set Tag; ``tags_of(reference)`` is the ``Tags``
of the VI a reference holds, made on first use. A saved value the conversion does
not decode is an ``UndecodedTag``, and reading it raises NotImplementedError naming
the VI and the tag (``codegen/nodes/tag_methods.py``).
"""

from __future__ import annotations

from lvkit.control_classes import class_ids
from lvkit.control_properties import (
    property_defaults,
    saved_defaults,
    undecoded_saved_members,
)

from .panel_objects_module import PANEL_OBJECTS_SOURCE
from .panels_application import APPLICATION_SOURCE
from .tree_items_module import TREE_ITEMS_SOURCE

PANELS_MODULE_NAME = "panels"
PANEL_NAME = "Panel"
PANEL_VALUES_NAME = "PanelValues"
PANEL_ERROR_NAME = "PanelError"
PANEL_OF_NAME = "panel_of"
AS_PANEL_NAME = "as_panel"
AS_CONTROL_NAME = "as_control"
AS_OBJECT_NAME = "as_object"
AS_PANE_NAME = "as_pane"
AS_OWNED_NAME = "as_owned"
CALL_CHAIN_NAME = "call_chain"
TAGS_NAME = "Tags"
TAGS_OF_NAME = "tags_of"
UNDECODED_TAG_NAME = "UndecodedTag"
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

_PANELS_MODULE_TEMPLATE = '''"""The front panel of a VI."""

from __future__ import annotations

import copy
import dataclasses
import inspect
import locale
import os
import sys
import threading
import time
import types
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from queue import Empty, Queue
from typing import Any, NoReturn

from .errors import LabVIEWError
from .live_io import LiveIO
from .variants import (
    LVType,
    Variant,
    variant_from_flattened,
    variant_to_flattened,
)

ABORT_POLL = 0.1

# NI's values of Front Panel Window:State (properties-and-methods/vi-server/vi/
# fpstate.html: 1 Standard, 2 Closed, 3 Hidden, 4 Minimized, 5 Maximized; 0 Invalid
# is an error when set) and the ones a window is opened or set to.
WINDOW_STANDARD = 1
WINDOW_CLOSED = 2
WINDOW_HIDDEN = 3
WINDOW_MINIMIZED = 4
WINDOW_MAXIMIZED = 5
WINDOW_OPEN_STATES = (
    WINDOW_STANDARD,
    WINDOW_HIDDEN,
    WINDOW_MINIMIZED,
    WINDOW_MAXIMIZED,
)

# NI's ClassID number of each VI Server class (the ClassID property,
# properties-and-methods/vi-server/generic/classid.html).
CLASS_IDS: dict[str, int] = __CLASS_IDS__

# The saved property a control of each VI Server class has when its panel lists none
# (``control_properties.json`` ``saved_defaults``).
SAVED_DEFAULTS: dict[str, dict[str, Any]] = __SAVED_DEFAULTS__

# The value of each property (by member name) on a control that was neither written
# nor saved with another (``control_properties.json`` ``default``), and the members
# whose saved starting value the conversion does not decode.
PROPERTY_DEFAULTS: dict[str, Any] = __PROPERTY_DEFAULTS__
UNDECODED_SAVED: frozenset[str] = frozenset(__UNDECODED_SAVED__)


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


def raise_empty_variant(label: str) -> NoReturn:
    """Error 1 for an empty variant given to a control that holds a value of a type
    (NI forum, LAVA topic 14464: Ctrl Val.Set with an empty variant gives error 1,
    "The invoke node probably wants to treat the incoming data as a variant
    wrapping the actual data type of the control")."""
    raise PanelError(
        code=1,
        source="Panel.set_variant",
        message=f'"{label}" was given an empty variant, which holds no value of '
        "its type",
    )


_NO_STATE = object()


__PANEL_OBJECTS__


class Control(GObject):
    """A control or indicator of a panel, as a reference to it: its ``label``, whether
    it is an ``indicator`` and its ``value``, which is the panel's value of it. A
    cluster's ``controls`` are a reference to each of its fields, in field order.
    ``class_name`` is its VI Server class (``Digital``, ``Enum``, ...), None when
    the panel records none."""

    def __init__(
        self,
        panel: PanelValues,
        name: str,
        label: str,
        indicator: bool,
        fields: Sequence[Any] | None = None,
        class_name: str | None = None,
        front_panel_class: str | None = None,
        element: Sequence[Any] | None = None,
    ) -> None:
        self.panel = panel
        self.name = name
        self.label = label
        self.indicator = indicator
        self.class_name = class_name
        self.front_panel_class = front_panel_class
        self._fields = (
            None
            if fields is None
            else [Field(self, label, key, *rest) for label, key, *rest in fields]
        )
        self._element = None if element is None else Element(self, *element)
        self._tree: TreeItems | None = None

    def _named(self) -> str:
        """The control as a message names it."""
        return self.label

    def _held(self, prop: str) -> Any:
        """The state of ``prop`` the panel holds for the control: the last write
        or the state the file's flags give, else what the control is saved with,
        else the default of its class (``_NO_STATE`` for none)."""
        held = self.panel.property_of(self.name, prop, _NO_STATE)
        if held is _NO_STATE:
            held = self.panel.saved.get(self.name, {}).get(prop, _NO_STATE)
        if held is _NO_STATE and self.class_name is not None:
            held = SAVED_DEFAULTS.get(self.class_name, {}).get(prop, _NO_STATE)
        if held is _NO_STATE and prop not in UNDECODED_SAVED:
            held = PROPERTY_DEFAULTS.get(prop, _NO_STATE)
        return held

    @property
    def value(self) -> Any:
        return self.panel.value(self.name)

    @value.setter
    def value(self, value: Any) -> None:
        self.panel.put(self.name, value)

    @property
    def variant(self) -> Variant:
        """The Value property of the reference: the value as a ``Variant`` (see
        ``PanelValues.get_variant``)."""
        return self.panel.variant(self.name)

    @variant.setter
    def variant(self, variant: Variant) -> None:
        self.panel.put_variant(self.name, variant)

    def get_property(self, prop: str) -> Any:
        """The state of the property ``prop`` of the control (``"strings"``, a Combo
        Box's drop-down list): the last write, else the state the control is saved
        in, else what it is saved with that a run cannot change (``saved``: an
        Enum's ``"strings"``). A control the panel holds no state of ``prop`` for is
        not one this property belongs to: error 1026."""
        held = self._held(prop)
        if held is _NO_STATE and self.name in self.panel.undecoded_strings:
            raise NotImplementedError(
                f"Strings[] of the Combo Box {self.label!r} read before any "
                f"write: its {self.panel.undecoded_strings[self.name]} saved "
                "strings are in a form the conversion does not decode"
            )
        if held is _NO_STATE and prop in UNDECODED_SAVED:
            raise NotImplementedError(
                f"{prop} of the control {self.label!r} read before any write: "
                "the value the file saves is in a form the conversion does not "
                "decode"
            )
        if held is _NO_STATE:
            raise PanelError(
                code=1026,
                source="Control.get_property",
                message=f'"{self._named()}" has no {prop} property.',
            )
        return copy.copy(held)

    def set_property(self, prop: str, value: Any) -> None:
        """Set the property ``prop`` of the control: the panel holds it and tells
        its views."""
        self.panel.set_property(self.name, prop, copy.copy(value))

    @property
    def tree(self) -> TreeItems:
        """The items of a Tree control, which hold every property and method of a
        Tree reference (``TreeItems``): the same object each time, started from what
        the file saves (``saved``); error 1026 for a control that is not a Tree."""
        if self.front_panel_class != "treeControl" and self.class_name != "Tree":
            raise PanelError(
                code=1026,
                source="Control.tree",
                message="Control Reference is invalid.",
            )
        if self._tree is None:
            saved = self.panel.saved.get(self.name, {})
            self._tree = TreeItems(
                self,
                saved.get("tree_items", ()),
                saved.get("tree_cell_widths", ()),
                saved.get("tree_active_column", 0),
                saved.get("tree_column_headers", ()),
            )
        return self._tree

    @property
    def class_id(self) -> int:
        """The ClassID property of the reference: NI's number of the control's VI
        Server class. A control whose front-panel class has no VI Server class in
        the conversion's table raises NotImplementedError naming the control and
        its front-panel class: the converter does not know it."""
        found = CLASS_IDS.get(self.class_name) if self.class_name else None
        if found is None:
            raise NotImplementedError(
                f"ClassID of the control {self._named()!r}: its front-panel class "
                f"{self.front_panel_class or self.class_name!r} has no VI Server "
                "class in the conversion's table"
            )
        return found

    @property
    def controls(self) -> list[Control]:
        """The Controls[] of a cluster:a reference to each of its fields, in
        cluster order, the same object each time; error 1026 for a control that is
        not a cluster."""
        if self._fields is None:
            raise PanelError(
                code=1026,
                source="Control.controls",
                message="Control Reference is invalid.",
            )
        return list(self._fields)

    @property
    def array_element(self) -> Control:
        """The Array Element property of an array: the reference to its element
        control, the same object each time; error 1026 for a control that is not
        an array."""
        if self._element is None:
            raise PanelError(
                code=1026,
                source="Control.array_element",
                message="Control Reference is invalid.",
            )
        return self._element


class Element(Control):
    """The element of the array control ``parent``, which its Array Element property
    gives a reference to (NI: "Reference to the array element. The properties of the
    array apply to all elements in the array. If you want to read the data value of
    a specific array element, use the Index Array function to index the array
    first."). It is a control of the element's own class (``class_name``, so its
    ``class_id``), with the properties the element is saved with (``saved``: an Enum
    element's ``"strings"``); what the panel records of the array alone (its value,
    its label, its states, its fields) is not the element's, so those raise
    NotImplementedError naming the array."""

    def __init__(
        self,
        parent: Control,
        class_name: str | None,
        front_panel_class: str | None,
        saved: Mapping[str, Any],
    ) -> None:
        super().__init__(
            parent.panel,
            parent.name,
            parent.label,
            parent.indicator,
            None,
            class_name,
            front_panel_class,
        )
        self.parent = parent
        self._saved = dict(saved)

    def _named(self) -> str:
        return f"an element of {self.parent.label}"

    def _unknown(self, what: str) -> NotImplementedError:
        return NotImplementedError(
            f"{what} of an element of the array {self.parent.label!r}: the "
            "conversion reads an element reference's class and saved properties "
            "only"
        )

    def _held(self, prop: str) -> Any:
        held = self._saved.get(prop, _NO_STATE)
        if held is _NO_STATE and self.class_name is not None:
            held = SAVED_DEFAULTS.get(self.class_name, {}).get(prop, _NO_STATE)
        return held

    @property
    def label(self) -> str:
        raise self._unknown("Label.Text")

    @label.setter
    def label(self, value: str) -> None:
        pass

    @property
    def value(self) -> Any:
        raise self._unknown("Value")

    @value.setter
    def value(self, value: Any) -> None:
        raise self._unknown("Value")

    @property
    def variant(self) -> Variant:
        raise self._unknown("Value")

    @variant.setter
    def variant(self, variant: Variant) -> None:
        raise self._unknown("Value")

    def set_property(self, prop: str, value: Any) -> None:
        raise self._unknown(f"Setting {prop}")

    @property
    def controls(self) -> list[Control]:
        raise self._unknown("Controls[]")


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

    @property
    def variant(self) -> Variant:
        """The field's value as a ``Variant`` with no type: the panel records the
        type of a control, not of the fields of a cluster."""
        return Variant(self.value)

    @variant.setter
    def variant(self, variant: Variant) -> None:
        if variant.type is None:
            raise_empty_variant(self.label)
        self.value = variant.value


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

    def geometry_changed(self, name: str, prop: str, value: Any) -> None:
        """The running VI moved the object ``name`` (``prop`` "position", value
        ``(left, top)``) or scrolled the pane ``name`` (``prop`` "origin", value
        ``(horizontal, vertical)``)."""

    def defer_updates_changed(self, deferred: bool) -> None:
        """The running VI set the panel's Defer Panel Updates: True, the view draws
        what is pending and then holds new changes back; False, it draws every
        change as it comes."""

    def window_changed(self, state: int, active: bool) -> None:
        """The VI's front panel window was opened, closed or given a state, by the
        program (FP.Open, FP.Close, FP.State, Is Frontmost): ``state`` is NI's
        Front Panel Window:State number (1 Standard, 2 Closed, 3 Hidden, 4
        Minimized, 5 Maximized) and ``active`` whether the window came to the
        front. A view that has a window for the panel shows, hides or closes
        it."""

    def window_property_changed(self, prop: str, value: Any) -> None:
        """The program wrote the property ``prop`` of the VI's front panel window:
        ``panel_bounds`` (``(left, top, right, bottom)``), ``origin`` (the scroll
        position, in the order of the cluster written), ``monitor``, ``behavior``
        or ``edit_mode_on_open``."""

    def subpanel_changed(self, control: str, vi: Any) -> None:
        """The program loaded the VI ``vi`` (a function) into the SubPanel control
        ``control`` (Insert VI), or took it out (Remove VI, ``vi`` None)."""

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

    def get_property(self, name: Any, prop: Any, value: Any) -> Any:
        """The panel's state of the property ``prop`` of ``name`` (including a
        Key Focus the user moved), else ``value``."""
        self._panel._checkpoint()
        return self._panel._live_get_property(key_of(name), key_of(prop), value)


class PanelValues(PanelLayout):
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
        types: Mapping[str, LVType] | None = None,
        variants: Sequence[str] | None = None,
        states: Mapping[str, Mapping[str, Any]] | None = None,
        classes: Mapping[str, str] | None = None,
        front_panel_classes: Mapping[str, str] | None = None,
        undecoded_strings: Mapping[str, int] | None = None,
        saved: Mapping[str, Mapping[str, Any]] | None = None,
        elements: Mapping[str, Sequence[Any]] | None = None,
        layout: Mapping[str, Any] | None = None,
    ) -> None:
        self.undecoded_strings = dict(undecoded_strings or {})
        self.saved = {name: dict(held) for name, held in (saved or {}).items()}
        clusters = clusters or {}
        classes = classes or {}
        front_panel_classes = front_panel_classes or {}
        elements = elements or {}
        self._properties: dict[str, dict[str, Any]] = {
            name: dict(held) for name, held in (states or {}).items()
        }
        self._controls = dict(controls or {})
        self._indicators = dict(indicators or {})
        self._defaults = dict(defaults or {})
        self._types = dict(types or {})
        self._variants = frozenset(variants or ())
        self._order = list(order or [*self._controls, *self._indicators])
        self._refs = {
            name: Control(
                self,
                name,
                label,
                name in self._indicators,
                clusters.get(name),
                classes.get(name),
                front_panel_classes.get(name),
                elements.get(name),
            )
            for name, label in {**self._controls, **self._indicators}.items()
        }
        self._build_layout(layout)
        self._values: MutableMapping[str, Any] = {} if storage is None else storage
        self._views: list[PanelView] = []
        self._lock = threading.RLock()
        self._defer_updates = False
        self._window_state = WINDOW_CLOSED
        self._window_values: dict[str, Any] = {}
        self._frontmost = False
        self._hosted_in: tuple[PanelValues, str] | None = None
        self._subpanels: dict[str, Any] = {}

    @property
    def window_state(self) -> int:
        """The Front Panel Window:State of the VI (NI: 1 Standard, 2 Closed, 3
        Hidden, 4 Minimized, 5 Maximized): Closed until the program opens the
        window (FP.Open, a write of Front Panel Window:Open) and after it closes
        it; Hidden while the panel is loaded in a SubPanel control (NI: "If you
        load the VI in a subpanel control, this property is read only and always
        returns a value of Hidden")."""
        with self._lock:
            return WINDOW_HIDDEN if self._hosted_in is not None else self._window_state

    def open_window(self, activate: bool = False, state: int = 1) -> None:
        """FP.Open: open the front panel window in ``state`` (NI: "If the front
        panel is already open, this method changes the state of the front panel
        window to the state you wire to this method"); ``activate`` opens it as
        the active window. 0 Invalid and 2 Closed are errors, as NI says ("the
        method returns an error"; NI gives no code, so this is the generic error 1,
        "An input parameter is invalid"). The views are told."""
        if state not in WINDOW_OPEN_STATES:
            raise PanelError(
                code=1,
                source="Panel.open_window",
                message=f"{state} is not a state a front panel window opens in.",
            )
        with self._lock:
            self._window_state = state
            self._frontmost = bool(activate)
        self._tell("window_changed", state, bool(activate))

    def close_window(self) -> None:
        """FP.Close: close the front panel window; the views are told."""
        with self._lock:
            self._window_state = WINDOW_CLOSED
            self._frontmost = False
        self._tell("window_changed", WINDOW_CLOSED, False)

    def set_window_state(self, state: int) -> None:
        """Write Front Panel Window:State. NI: "If you attempt to set this property
        for a front panel window that is not open, the property returns an error",
        and Invalid and Closed "return an error" when set; the VI Server's error
        for a VI not in a state that allows the operation is 1000 (NI, General
        LabVIEW Error Codes: "The VI is not in a state compatible with this
        operation"), and an input that is not allowed is error 1. A VI loaded in a
        SubPanel control has a read-only state."""
        with self._lock:
            hosted = self._hosted_in is not None
            closed = self._window_state == WINDOW_CLOSED
        if hosted or closed:
            raise PanelError(
                code=1000,
                source="Panel.set_window_state",
                message="The VI is not in a state compatible with this operation: "
                "the front panel window is not open.",
            )
        if state not in WINDOW_OPEN_STATES:
            raise PanelError(
                code=1,
                source="Panel.set_window_state",
                message=f"{state} is not a state a front panel window is set to.",
            )
        with self._lock:
            self._window_state = state
        self._tell("window_changed", state, False)

    @property
    def frontmost(self) -> bool:
        """Whether the front panel window is the front window: it was opened as the
        active window or brought to the front, and is open and not hidden or
        minimized. The window system, when a view has one, knows better: a view
        tells the panel through ``window_changed`` only what the program did."""
        with self._lock:
            return self._frontmost and self.window_state in (
                WINDOW_STANDARD,
                WINDOW_MAXIMIZED,
            )

    def bring_to_front(self) -> None:
        """Write TRUE to Front Panel Window:Is Frontmost: the window comes to the
        front (a write of FALSE has no effect, NI)."""
        with self._lock:
            self._frontmost = True
            state = self._window_state
        self._tell("window_changed", state, True)

    def window_value(self, prop: str, default: Any) -> Any:
        """The last written value of the window property ``prop`` (``panel_bounds``,
        ``origin``, ``monitor``, ``behavior``, ``edit_mode_on_open``), else
        ``default``."""
        with self._lock:
            return self._window_values.get(prop, default)

    def set_window_value(self, prop: str, value: Any) -> None:
        """Write the window property ``prop``: the panel holds it and tells the
        views."""
        with self._lock:
            self._window_values[prop] = value
        self._tell("window_property_changed", prop, value)

    def insert_vi(self, control: str, vi: Any) -> None:
        """SubPanel Insert VI on the control ``control``: load the front panel of
        the VI ``vi`` (a reference: its function) into it. NI: "If the front panel
        of the VI you want to load is open or if you loaded the front panel in
        another subpanel control on the same front panel, LabVIEW returns an error"
        (no code is given: error 1000, as for a VI not in a state that allows the
        operation); the panel the control holds before is unloaded first. The views
        are told, and one that has a widget for the SubPanel shows the VI's
        panel."""
        panel = panel_of(vi)
        with self._lock:
            loaded = panel is not self._subpanels.get(control)
            busy = panel._window_state != WINDOW_CLOSED or panel._hosted_in is not None
            if loaded and busy:
                raise PanelError(
                    code=1000,
                    source="Panel.insert_vi",
                    message="The VI is not in a state compatible with this "
                    "operation: its front panel is open or loaded in a SubPanel "
                    "control.",
                )
            before = self._subpanels.get(control)
            if before is not None and before is not panel:
                before._hosted_in = None
            self._subpanels[control] = panel
            panel._hosted_in = (self, control)
        self._tell("subpanel_changed", control, vi)

    def remove_vi(self, control: str) -> None:
        """SubPanel Remove VI on the control ``control``: unload the VI it holds
        (nothing when it holds none)."""
        with self._lock:
            panel = self._subpanels.pop(control, None)
            if panel is not None:
                panel._hosted_in = None
        self._tell("subpanel_changed", control, None)

    def abort(self) -> None:
        """Abort VI on a panel that is not a VI's function: a Global VI has no
        diagram to abort (NI's Abort VI page lists "Available with global VIs:
        No"), so it is error 1035, as Run VI on it is."""
        raise PanelError(
            code=1035,
            source="Panel.abort",
            message="Operation is invalid for this type of VI.",
        )

    @property
    def defer_updates(self) -> bool:
        """The panel's Defer Panel Updates (NI: TRUE defers new requests for front
        panel updates, FALSE redraws changed elements immediately): the last value
        the VI set, else False."""
        with self._lock:
            return self._defer_updates

    @defer_updates.setter
    def defer_updates(self, value: bool) -> None:
        with self._lock:
            self._defer_updates = bool(value)
        self._tell("defer_updates_changed", bool(value))

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

    def set_variant(self, label: str, variant: Variant) -> None:
        """Set the control or indicator labelled ``label`` to the data of
        ``variant``, as Ctrl Val.Set does (see ``put_variant``)."""
        self.put_variant(self._name_of(label), variant)

    def get_variant(self, label: str) -> Variant:
        """The value of the control or indicator labelled ``label`` as a
        ``Variant``, as Ctrl Val.Get does (see ``variant``)."""
        return self.variant(self._name_of(label))

    def get_all_variants(self, controls: bool = False) -> list[tuple[str, Variant]]:
        """``get_all`` with each value a ``Variant`` (see ``variant``)."""
        with self._lock:
            names = self._controls if controls else self._indicators
            return [
                (names[name], self._variant(name))
                for name in self._order
                if names.get(name)
            ]

    def set_flattened(self, label: str, type_string: Any, data: str) -> None:
        """Set the control or indicator labelled ``label`` to the value ``data``
        holds flattened as the type ``type_string`` describes, as Ctrl Val.Set
        (Deprecated) does: ``set_variant`` of the variant they make."""
        self.set_variant(label, variant_from_flattened(data, type_string))

    def get_all_flattened(
        self, controls: bool = False
    ) -> list[tuple[str, list[int], str]]:
        """``get_all_variants`` as Ctrl Val.Get All (Deprecated) gives it: each
        value as ``(label, type descriptor, flattened data)``, the descriptor the
        type string of the type the panel records for the control (a control that
        records none, or a type with no flattened form, raises
        ``NotImplementedError`` naming the control)."""
        flattened = []
        for label, variant in self.get_all_variants(controls):
            try:
                descriptor, data = variant_to_flattened(
                    Variant(variant.value, variant.type)
                )
            except NotImplementedError as error:
                raise NotImplementedError(
                    f"Ctrl Val.Get All (Deprecated): the control {label!r}: {error}"
                ) from error
            flattened.append((label, descriptor, data))
        return flattened

    @property
    def controls(self) -> list[Control]:
        """The Controls[] of the panel: a reference to each control and indicator,
        in tabbing order."""
        return [self._refs[name] for name in self._order]

    def control_of(self, name: str) -> Control:
        """The reference to the control or indicator ``name`` (the one a Property Node
        or Invoke Node bound to it acts on); error 1026 for a name the panel does not
        hold."""
        if name not in self._refs:
            raise PanelError(
                code=1026,
                source="PanelValues.control_of",
                message="Control Reference is invalid.",
            )
        return self._refs[name]

    def holds(self, name: str) -> bool:
        """Whether ``name`` is the name of a control or indicator of the panel."""
        return name in self._controls or name in self._indicators

    def property_of(self, name: str, prop: str, default: Any = None) -> Any:
        """The state of the property ``prop`` (``"visible"``, ``"disabled"``,
        ``"key_focus"``, ...) of the control or indicator ``name``: the last value
        the VI set or a view noted, else the state the control is stored in, else
        ``default``."""
        self._check(name)
        with self._lock:
            return self._properties.get(name, {}).get(prop, default)

    def properties_of(self, name: str) -> dict[str, Any]:
        """Every property state the panel holds for ``name``."""
        self._check(name)
        with self._lock:
            return dict(self._properties.get(name, {}))

    def set_property(self, name: str, prop: str, value: Any) -> None:
        """Set the property ``prop`` of the control or indicator ``name``: the panel
        holds it (a view that is built later starts from it) and tells the
        views."""
        self._check(name)
        with self._lock:
            self._properties.setdefault(name, {})[prop] = value
        self._tell("property_changed", name, prop, value)

    def note_property(self, name: str, prop: str, value: Any) -> None:
        """A view tells the panel that the user changed the property ``prop`` of
        ``name`` (the control got the keyboard focus). No view is told."""
        self._check(name)
        with self._lock:
            self._properties.setdefault(name, {})[prop] = value

    def put(self, name: str, value: Any) -> None:
        """Set the control or indicator ``name`` and tell the views."""
        self._check(name)
        with self._lock:
            self._values[name] = value
        self._tell("control_changed", name, value)

    def put_variant(self, name: str, variant: Variant) -> None:
        """Set the control or indicator ``name`` to ``variant``: a control that
        holds a variant stores it; any other holds the data of the variant, and an
        empty variant has none (error 1)."""
        self._check(name)
        if name in self._variants:
            self.put(name, variant)
        elif variant.type is None:
            raise_empty_variant(self._refs[name].label)
        else:
            self.put(name, variant.value)

    def value(self, name: str) -> Any:
        """The value of the control or indicator ``name``."""
        self._check(name)
        with self._lock:
            return self._value(name)

    def variant(self, name: str) -> Variant:
        """The value of the control or indicator ``name`` as a ``Variant``: a
        control that holds a variant gives it (``Variant()`` while it holds none);
        any other gives its value with the type the panel records for it (a
        ``Variant`` of no type where the panel records none)."""
        self._check(name)
        with self._lock:
            return self._variant(name)

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
        """The panel's value of ``name``. A control or indicator with a default
        made for it holds that from the first read on, as a front panel holds one
        value (an indicator no run has written holds its type's default, a
        class's default object, not nothing); else the control's own default
        (``_own_default``), else None."""
        if name in self._values:
            return self._values[name]
        factory = self._defaults.get(name)
        if factory is not None:
            self._values[name] = factory()
            return self._values[name]
        if name in self._controls:
            return self._own_default(name)
        return None

    def _variant(self, name: str) -> Variant:
        value = self._value(name)
        if name in self._variants:
            if isinstance(value, Variant):
                return value
            return Variant() if value is None else Variant(value)
        return Variant(value, self._types.get(name))

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
        types: Mapping[str, LVType] | None = None,
        variants: Sequence[str] | None = None,
        states: Mapping[str, Mapping[str, Any]] | None = None,
        classes: Mapping[str, str] | None = None,
        front_panel_classes: Mapping[str, str] | None = None,
        undecoded_strings: Mapping[str, int] | None = None,
        saved: Mapping[str, Mapping[str, Any]] | None = None,
        elements: Mapping[str, Sequence[Any]] | None = None,
        layout: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            controls,
            indicators,
            defaults,
            order=order,
            clusters=clusters,
            types=types,
            variants=variants,
            states=states,
            classes=classes,
            front_panel_classes=front_panel_classes,
            undecoded_strings=undecoded_strings,
            saved=saved,
            elements=elements,
            layout=layout,
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

    def _called_by_a_vi(self) -> bool:
        """Whether this thread is inside this VI's function called by another VI's
        function: the VI is then a subVI of the running one."""
        own = getattr(self.function, "vi_name", None)
        seen = False
        frame: types.FrameType | None = sys._getframe(1)
        while frame is not None:
            name = getattr(_function_of(frame), "vi_name", None)
            if isinstance(name, str):
                if seen:
                    return True
                seen = name == own
            frame = frame.f_back
        return False

    def abort(self) -> None:
        """Abort VI. NI: "Aborts the execution of a top-level VI. This method returns
        error 1000 if you call it on a subVI. Otherwise, this method is similar to
        pressing the Abort Execution button": a VI whose function this thread is
        inside, called by another VI's function, is error 1000. Else the run in
        progress ends at its next point (a ``live_io`` call or a wait for an
        event); a paused run is released so it reaches that point. A VI that takes
        no ``live_io`` has no such point and runs to its end, and a VI that is not
        running has nothing to abort."""
        if self._called_by_a_vi():
            raise PanelError(
                code=1000,
                source="Panel.abort",
                message="The VI is not in a state compatible with this "
                "operation: it is a subVI.",
            )
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
        """The function set the property ``prop`` of ``name``: the panel holds it
        (a view that is built later starts from it) and tells the views."""
        if self.holds(name):
            self.set_property(name, prop, value)

    def _live_get_property(self, name: str, prop: str, held: Any) -> Any:
        """The panel's state of the property ``prop`` of ``name``; ``held`` (the
        function's own) when the panel has none."""
        if not self.holds(name):
            return held
        return self.property_of(name, prop, held)

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


def _function_of(frame: types.FrameType) -> Any:
    """The function object the ``frame`` runs, found by its code through the
    module's globals (``co_qualname``: ``f`` for a function, ``Class.m`` for a
    method, a property's getter or setter); None for code that is not reachable
    that way (a nested function, a lambda, a comprehension)."""
    found: Any = frame.f_globals
    for part in frame.f_code.co_qualname.split("."):
        if part == "<locals>" or part.startswith("<"):
            return None
        found = (
            found.get(part) if isinstance(found, dict) else getattr(found, part, None)
        )
        if found is None:
            return None
    candidates = (
        found,
        getattr(found, "fget", None),
        getattr(found, "fset", None),
        getattr(found, "__func__", None),
    )
    for candidate in candidates:
        if getattr(candidate, "__code__", None) is frame.f_code:
            return candidate
    return None


def call_chain() -> list[str]:
    """Call Chain: the ``vi_name`` of each VI function on this thread's stack, the
    function that called ``call_chain`` first and the top-level VI last. Code that
    is not a VI function (a helper, a test, ``Panel.run``) is skipped; a VI that
    runs in another thread has the stack of that thread, which ends at its top VI."""
    names: list[str] = []
    frame: types.FrameType | None = sys._getframe(1)
    while frame is not None:
        name = getattr(_function_of(frame), "vi_name", None)
        if isinstance(name, str):
            names.append(name)
        frame = frame.f_back
    return names


class UndecodedTag:
    """The value of a saved tag the conversion did not decode, with the reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


class Tags:
    """The tags of a VI: each name with its ``Variant``, and which tags are saved
    with the file (persistent). A tag the file saves is persistent; Set Tag gives
    the persistence of the tag it writes."""

    def __init__(
        self, owner: str, saved: Mapping[str, Variant | UndecodedTag] | None = None
    ) -> None:
        self.owner = owner
        self._values: dict[str, Variant | UndecodedTag] = dict(saved or {})
        self._persistent = set(self._values)

    def _read(self, name: str) -> Variant:
        value = self._values[name]
        if isinstance(value, UndecodedTag):
            raise NotImplementedError(
                f"Tag {name!r} of {self.owner} is saved in a form the conversion "
                f"does not decode: {value.reason}"
            )
        return value

    def get(self, name: str) -> tuple[bool, bool, Variant]:
        """``(found, is_persistent, value)`` of the tag ``name``: a tag that does
        not exist is not found, with no value (an empty Variant)."""
        if name not in self._values:
            return False, False, Variant()
        return True, name in self._persistent, self._read(name)

    def set(
        self, name: str, value: Variant, persistent: bool = False, old: bool = False
    ) -> tuple[bool, Variant]:
        """Set the tag ``name`` and give ``(True, the value it had)`` (an empty
        Variant when it had none); ``old`` is whether the caller reads that value,
        which is an error for a tag saved in a form the conversion does not
        decode."""
        before = self._read(name) if old and name in self._values else Variant()
        self._values[name] = value
        if persistent:
            self._persistent.add(name)
        else:
            self._persistent.discard(name)
        return True, before

    def remove(self, name: str, old: bool = False) -> tuple[bool, Variant]:
        """Remove the tag ``name`` and give ``(removed, the value it had)``; a tag
        that does not exist is not removed (``old`` as in ``set``)."""
        if name not in self._values:
            return False, Variant()
        before = self._read(name) if old else Variant()
        del self._values[name]
        self._persistent.discard(name)
        return True, before


def tags_of(reference: Any) -> Tags:
    """The tags of the VI ``reference`` holds (its function), made on first use;
    error 1026 for a reference that holds no VI."""
    tags = getattr(reference, "tags", None)
    if isinstance(tags, Tags):
        return tags
    owner = getattr(reference, "vi_name", None)
    if isinstance(owner, str):
        reference.tags = Tags(owner)
        return reference.tags
    raise PanelError(code=1026, source="tags_of", message="VI Reference is invalid.")


__TREE_ITEMS__

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


def as_object(reference: Any) -> GObject:
    """``reference`` as the reference to a front-panel object (a control, an
    indicator, a decoration or a pane); error 1026 for a reference that holds
    none."""
    if not isinstance(reference, GObject):
        raise PanelError(
            code=1026, source="as_object", message="Object Reference is invalid."
        )
    return reference


def as_owned(reference: Any) -> GObject | PanelValues:
    """``reference`` as the reference to an object that has an owner: a front-panel
    object or the panel itself (the top-level object); error 1026 for a reference
    that holds none."""
    if not isinstance(reference, (GObject, PanelValues)):
        raise PanelError(
            code=1026, source="as_owned", message="Object Reference is invalid."
        )
    return reference


def as_pane(reference: Any) -> Pane:
    """``reference`` as the reference to a pane of a panel (an element of
    ``Panel.panes``); error 1026 for a reference that holds none."""
    if not isinstance(reference, Pane):
        raise PanelError(
            code=1026, source="as_pane", message="Pane Reference is invalid."
        )
    return reference
'''


PANELS_MODULE_SOURCE = (
    _PANELS_MODULE_TEMPLATE.replace("__CLASS_IDS__", repr(class_ids()))
    .replace("__SAVED_DEFAULTS__", repr(saved_defaults()))
    .replace("__PROPERTY_DEFAULTS__", repr(property_defaults()))
    .replace("__UNDECODED_SAVED__", repr(sorted(undecoded_saved_members())))
    .replace("__TREE_ITEMS__", TREE_ITEMS_SOURCE.strip("\n"))
    .replace("__PANEL_OBJECTS__", PANEL_OBJECTS_SOURCE.strip("\n"))
    + APPLICATION_SOURCE
)
