"""The properties of a front-panel control that a bound Property Node writes.

A Property Node bound to a control or indicator of its own VI (no reference
wired) names each row by the property's code (``PropertySegment.code``), never by
the stored name, which is the author's choice of short or long. A built-in
control class shares one code per property across the classes that inherit it; a
class can define its own code under a common name (a Combo Box's ``Value``), so
``control_properties.json`` lists inherited codes and per-class codes, and a class
entry wins over an inherited one. A property is listed only when NI's public
documentation says what it does.

A Property Node on a wired control reference (no binding) reads a few properties
that are the reference's own: its ``Value``, its ``Indicator`` flag, its
``Label.Text``, its ``ClassID`` and, of a cluster, its ``Controls[]``
(``ReferenceRow``).

The graph (which VIs touch their panel while running) and code generation (what a
row generates) both read this, so it sits outside both.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ._data import data_dir

_PATH = data_dir() / "control_properties.json"


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
    staying a cluster); ``cluster`` (the cluster the wire carries)."""

    name: str
    member: str
    value: str
    doc_url: str


class ReferenceRow(Enum):
    """What a row of a Property Node on a control reference is, when it is one of
    the control's own: its ``Value``, whether it is an ``Indicator``, the text of
    its ``Label``, its ``ClassID`` (``control_classes.json``) and, of a cluster, the
    references to its ``Controls[]``."""

    VALUE = "value"
    INDICATOR = "indicator"
    LABEL_TEXT = "label_text"
    CLUSTER_CONTROLS = "cluster_controls"
    CLASS_ID = "class_id"


@dataclass(frozen=True)
class _Table:
    inherited: dict[str, ControlProperty]
    classes: dict[str, dict[str, ControlProperty]]
    inherited_values: frozenset[int]
    class_values: dict[str, frozenset[int]]
    inherited_signaling: frozenset[int]
    class_signaling: dict[str, frozenset[int]]
    reference_rows: dict[tuple[int, ...], ReferenceRow]
    reference_properties: dict[str, frozenset[str]]
    reinit_to_default: frozenset[int]


def _entries(raw: dict[str, dict[str, str]]) -> dict[str, ControlProperty]:
    return {
        code: ControlProperty(
            entry["name"], entry["member"], entry["value"], entry["doc_url"]
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
        reference_properties={
            name: frozenset(members)
            for name, members in raw["reference_properties"].items()
            if not name.startswith("_")
        },
        reinit_to_default=frozenset(raw["methods"]["reinit_to_default"]["codes"]),
    )


def is_reinit_to_default(method_code: int | None) -> bool:
    """Whether an Invoke Node's method code is the control method Reinit To
    Default (the one code every control class shares). NI: "Reinitializes the
    value of the control to the default value." (the ``methods`` entry's
    ``doc_url``)."""
    return method_code in _table().reinit_to_default


def control_property(class_name: str, codes: Sequence[int]) -> ControlProperty | None:
    """The listed property that the row with property ``codes`` (one per path
    segment) of a Property Node bound to a control of class ``class_name`` is; None
    for one that is not listed. A listed property is found by its codes joined with
    ``.`` (a one-segment row by its code), the class's own entry before an
    inherited one."""
    table = _table()
    key = ".".join(str(code) for code in codes)
    return table.classes.get(class_name, {}).get(key) or table.inherited.get(key)


def reference_property(
    class_name: str, codes: Sequence[int]
) -> ControlProperty | None:
    """The listed property that the row with property ``codes`` on a wired control
    reference of class ``class_name`` is, when the panel reads and writes it for
    that class (``reference_properties`` of ``control_properties.json``); None for
    any other row."""
    listed = control_property(class_name, codes)
    if listed is None:
        return None
    allowed = _table().reference_properties.get(class_name, frozenset())
    return listed if listed.member in allowed else None


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
    class's strict or own one), ``Indicator``, ``Label.Text`` or a cluster's
    ``Controls[]``; None for any other row."""
    if is_value_row(class_name, codes):
        return ReferenceRow.VALUE
    return _table().reference_rows.get(tuple(codes))
