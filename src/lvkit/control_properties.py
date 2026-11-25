"""The properties of a front-panel control that a bound Property Node writes.

A Property Node bound to a control or indicator of its own VI (no reference
wired) names each row by the property's code (``PropertySegment.code``), never by
the stored name, which is the author's choice of short or long. A built-in
control class shares one code per property across the classes that inherit it; a
class can define its own code under a common name (a Combo Box's ``Value``), so
``control_properties.json`` lists inherited codes and per-class codes, and a class
entry wins over an inherited one. A property is listed once, on the class NI
documents it on, and a class finds it there through its ancestors in NI's class
hierarchy (``class_parents``), a strict and a non-strict reference of a class being
one class. A property is listed only when NI's public documentation says what it
does.

A Property Node on a wired control reference (no binding) reads a few properties
that are the reference's own: its ``Value``, its ``Indicator`` flag, its
``Label.Text``, its ``ClassID`` and, of a cluster, its ``Controls[]`` and, of an
array, its ``Array Element`` (``ReferenceRow``).

The graph (which VIs touch their panel while running) and code generation (what a
row generates) both read this, so it sits outside both.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ._data import data_dir

_PATH = data_dir() / "control_properties.json"
_CONTROL = "Control"
_STRICT_SUFFIX = " (strict)"

STATE_MEMBERS = ("visible", "disabled", "strings")
"""The ``ControlProperty.member`` of the properties that start from the stored
state of the control, which the conversion decodes: ``Visible`` and ``Disabled``
from the flags, ``Strings[]`` of a Combo Box or a Ring from its items."""

FIELD_MEMBERS = (
    "tip_strip",
    "doc_max",
    "x_min",
    "x_max",
    "y_min",
    "y_max",
    "index_values",
    "item_names",
    "active_cell",
    "tree_items",
    "tree_cell_widths",
    "tree_active_column",
    "tree_column_headers",
)
"""The ``ControlProperty.member`` of the settable properties that start from a field
of the control's ddo, which the conversion decodes (``FPTerminal.starting_properties``,
``parser/fp_starting_properties.py``). The panel holds the value as saved, which a
write replaces."""

SAVED_MEMBERS = ("strings", "mech_action")
"""The ``ControlProperty.member`` of the properties a control is saved with and a
running VI cannot change, which the conversion decodes (an Enum's ``Strings[]``,
a Boolean's ``MechAction``)."""


@dataclass(frozen=True)
class ControlProperty:
    """One listed property: ``name`` is NI's long name, ``member`` the Python name
    (the generated property enum's member is its upper-case form), ``value`` the
    kind written and ``doc_url`` NI's page. ``value`` is the Python value the
    generated code passes: ``bool``; ``enum`` (the property's integer enum value);
    ``number`` (int or float); ``string`` (str); ``path`` (``pathlib.Path``);
    ``color`` (int, LabVIEW's ``0x00RRGGBB`` color); ``string_array`` (list of
    str, of lists of str for a two-dimensional array); ``number_array`` (list of
    int or float); ``array`` (list of the elements the wire carries, a cluster
    staying a cluster); ``cluster`` (the cluster the wire carries).
    ``settable_while_running`` is NI's "Settable when the VI is running": False
    for a property the file saves and a running VI only reads (an Enum's
    ``Strings[]``). A property is the same whether a Property Node bound to a
    control or one on a wired control reference reads it."""

    name: str
    member: str
    value: str
    doc_url: str
    settable_while_running: bool = True
    has_default: bool = False
    default: Any = None
    holder: str | None = None


@dataclass(frozen=True)
class TreeMethod:
    """One method of a Tree control (``tree_methods`` of ``control_properties.json``):
    ``member`` the method of the control's ``TreeItems``, ``params`` its parameters in
    NI's order with the Python text of each default, ``returns`` whether it gives a
    value."""

    name: str
    member: str
    params: tuple[tuple[str, str], ...]
    returns: bool
    doc_url: str


class ReferenceRow(Enum):
    """What a row of a Property Node on a control reference is, when it is one of
    the control's own: its ``Value``, whether it is an ``Indicator``, the text of
    its ``Label``, its ``ClassID`` (``control_classes.json``) and, of a cluster, the
    references to its ``Controls[]``, ``Decos[]`` and ``AllObjs[]`` and, of an array,
    the reference to its ``Array Element``."""

    CLUSTER_DECORATIONS = "cluster_decorations"
    CLUSTER_ALL_OBJECTS = "cluster_all_objects"
    VALUE = "value"
    INDICATOR = "indicator"
    LABEL_TEXT = "label_text"
    CLUSTER_CONTROLS = "cluster_controls"
    CLASS_ID = "class_id"
    ARRAY_ELEMENT = "array_element"


@dataclass(frozen=True)
class _Table:
    inherited: dict[str, ControlProperty]
    classes: dict[str, dict[str, ControlProperty]]
    inherited_values: frozenset[int]
    class_values: dict[str, frozenset[int]]
    inherited_signaling: frozenset[int]
    class_signaling: dict[str, frozenset[int]]
    reference_rows: dict[tuple[int, ...], ReferenceRow]
    unsettable_rows: dict[tuple[int, ...], str]
    class_parents: dict[str, str | None]
    block_diagram_roots: frozenset[str]
    generic_classes: frozenset[str]
    saved_defaults: dict[str, dict[str, Any]]
    reinit_to_default: frozenset[int]
    tree_methods: dict[int, TreeMethod]


def _entries(raw: dict[str, dict[str, Any]]) -> dict[str, ControlProperty]:
    return {
        code: ControlProperty(
            entry["name"],
            entry["member"],
            entry["value"],
            entry["doc_url"],
            entry.get("settable_while_running", True),
            "default" in entry,
            entry.get("default"),
            entry.get("holder"),
        )
        for code, entry in raw.items()
    }


@functools.cache
def _table() -> _Table:
    with open(_PATH, encoding="utf-8") as handle:
        raw = json.load(handle)
    values = raw["value_codes"]
    signaling = raw["value_signaling_codes"]
    return _Table(
        inherited=_entries(raw["inherited"]),
        classes={name: _entries(entries) for name, entries in raw["classes"].items()},
        inherited_values=frozenset(values["inherited"]),
        class_values={
            name: frozenset(codes) for name, codes in values["classes"].items()
        },
        inherited_signaling=frozenset(signaling["inherited"]),
        class_signaling={
            name: frozenset(codes) for name, codes in signaling["classes"].items()
        },
        reference_rows={
            tuple(path): ReferenceRow(name)
            for name, entry in raw["reference_rows"].items()
            for path in entry["codes"]
        },
        unsettable_rows={
            tuple(path): entry["doc_url"]
            for entry in raw["reference_rows"].values()
            if not entry.get("settable_while_running", True)
            for path in entry["codes"]
        },
        class_parents={
            name: parent
            for name, parent in raw["class_parents"].items()
            if not name.startswith("_")
        },
        block_diagram_roots=frozenset(raw["block_diagram_roots"]["classes"]),
        generic_classes=frozenset(raw["generic_classes"]["classes"]),
        saved_defaults={
            name: dict(members)
            for name, members in raw["saved_defaults"].items()
            if not name.startswith("_")
        },
        reinit_to_default=frozenset(raw["methods"]["reinit_to_default"]["codes"]),
        tree_methods={
            int(code): TreeMethod(
                entry["name"],
                entry["member"],
                tuple((name, default) for name, default in entry["params"]),
                entry["returns"],
                entry["doc_url"],
            )
            for code, entry in raw["tree_methods"].items()
            if not code.startswith("_")
        },
    )


def tree_method(method_code: int | None) -> TreeMethod | None:
    """The method of a Tree control that an Invoke Node's method code is; None for any
    other method."""
    return _table().tree_methods.get(method_code or 0)


TREE_HOLDER = "tree"
"""The ``holder`` of a property that is a member of the control's ``TreeItems``."""

POINT_TO_ROW_COLUMN_CODE = 1969895445
"""The method code of Point To Row Column, a Tree method with no Python meaning: NI
"Converts a pixel coordinate to a tag-column pair in the coordinates of the control"
(``treecontrol/pttorowcol.html``), a pixel of the control's own drawing."""


def saved_defaults() -> dict[str, dict[str, Any]]:
    """The value of each saved property (``ControlProperty.settable_while_running``
    False) on a control of each VI Server class that the control has when nothing
    else is recorded: ``{"Boolean": {"mech_action": 0}}``."""
    return {name: dict(members) for name, members in _table().saved_defaults.items()}


def _every_entry() -> list[ControlProperty]:
    table = _table()
    return [
        *table.inherited.values(),
        *(entry for entries in table.classes.values() for entry in entries.values()),
    ]


def property_defaults() -> dict[str, Any]:
    """The value each listed property has on a control that was neither written nor
    saved with another (a ``default`` in ``control_properties.json``: LabVIEW's
    state of a new control, or what NI's page says), by member name."""
    return {
        entry.member: (
            tuple(entry.default) if entry.value == "cluster" else entry.default
        )
        for entry in _every_entry()
        if entry.has_default
    }


def undecoded_saved_members() -> frozenset[str]:
    """The members of the listed properties that start from a value the file saves
    and the conversion does not decode: every listed member with no ``default`` that
    is not one of ``STATE_MEMBERS``, ``FIELD_MEMBERS`` or ``SAVED_MEMBERS``. A read
    of one before any write is a run-time NotImplementedError naming it, not a
    guess."""
    decoded = {*STATE_MEMBERS, *FIELD_MEMBERS, *SAVED_MEMBERS}
    return frozenset(
        entry.member
        for entry in _every_entry()
        if not entry.has_default
        and entry.member not in decoded
        and entry.holder is None
    )


def is_reinit_to_default(method_code: int | None) -> bool:
    """Whether an Invoke Node's method code is the control method Reinit To
    Default (the one code every control class shares). NI: "Reinitializes the
    value of the control to the default value." (the ``methods`` entry's
    ``doc_url``)."""
    return method_code in _table().reinit_to_default


def _ancestry(class_name: str) -> list[str]:
    """``class_name`` and the classes above it, nearest first, up to ``Control``
    (``class_parents``): a class with no parent listed is a class of Control, a
    strict reference of a class is that class."""
    parents = _table().class_parents
    name: str | None = class_name.removesuffix(_STRICT_SUFFIX)
    chain: list[str] = []
    while name is not None and name not in chain:
        chain.append(name)
        name = parents.get(name, _CONTROL)
    return chain


def is_block_diagram_class(class_name: str) -> bool:
    """Whether ``class_name`` is a class of an object of a block diagram (a SubVI
    node, a Diagram, a Wire, a Terminal, ...): the class, or a class above it in
    ``class_parents``, is one of ``block_diagram_roots``, the classes NI's hierarchy
    lists under GObject that are block-diagram objects. A class the hierarchy does
    not place (Gen, GObj) is not."""
    roots = _table().block_diagram_roots
    return any(name in roots for name in _ancestry(class_name))


def is_generic_object_class(class_name: str) -> bool:
    """Whether ``class_name`` is Generic or GObject, whose reference can be any
    front-panel or block-diagram object (``generic_classes``)."""
    return class_name in _table().generic_classes


def control_property(class_name: str, codes: Sequence[int]) -> ControlProperty | None:
    """The listed property that the row with property ``codes`` (one per path
    segment) of a Property Node bound to a control of class ``class_name`` is; None
    for one that is not listed. A listed property is found by its codes joined with
    ``.`` (a one-segment row by its code), on the class's own entry, else on the
    entry of each class above it (``class_parents``), else among the ``inherited``
    properties of Control."""
    table = _table()
    key = ".".join(str(code) for code in codes)
    for name in _ancestry(class_name):
        entries = table.inherited if name == _CONTROL else table.classes.get(name, {})
        if key in entries:
            return entries[key]
    return None


def is_value_row(class_name: str, codes: Sequence[int]) -> bool:
    """Whether the row with property ``codes`` is the control's ``Value``."""
    if len(codes) != 1:
        return False
    table = _table()
    return codes[0] in table.inherited_values or codes[0] in table.class_values.get(
        class_name, frozenset()
    )


def is_value_signaling_row(class_name: str, codes: Sequence[int]) -> bool:
    """Whether the row with property ``codes`` is the control's ``Value
    (Signaling)``: it writes the value the way ``Value`` does and also generates a
    Value Change event, as if the user had changed the control (NI: write only)."""
    if len(codes) != 1:
        return False
    table = _table()
    own = table.class_signaling.get(class_name, frozenset())
    return codes[0] in table.inherited_signaling or codes[0] in own


def reference_row(class_name: str, codes: Sequence[int]) -> ReferenceRow | None:
    """The own property of a control reference that the row with property ``codes``
    on a reference of class ``class_name`` is: ``Value`` (the generic code, or the
    class's strict or own one), ``Indicator``, ``Label.Text``, ``ClassID``, a
    cluster's ``Controls[]`` or an array's ``Array Element``; None for any other
    row."""
    if is_value_row(class_name, codes):
        return ReferenceRow.VALUE
    return _table().reference_rows.get(tuple(codes))


def unsettable_reference_page(codes: Sequence[int]) -> str | None:
    """NI's page for the own property of a control reference that the row with
    property ``codes`` is, when the page says it cannot be set while the VI runs
    (``settable_while_running`` false: ``Indicator``, ``Label.Text`` of an owned
    label, ``ClassID``, ``Controls[]``, ``Array Element``); None for any other
    row."""
    return _table().unsettable_rows.get(tuple(codes))
