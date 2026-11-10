"""The properties of a front-panel control that a bound Property Node writes.

A Property Node bound to a control or indicator of its own VI (no reference
wired) names each row by the property's code (``PropertySegment.code``), never by
the stored name, which is the author's choice of short or long. A built-in
control class shares one code per property across the classes that inherit it; a
class can define its own code under a common name (a Combo Box's ``Value``), so
``control_properties.json`` lists inherited codes and per-class codes, and a class
entry wins over an inherited one. A property is listed only when NI's public
documentation says what it does.

The graph (which VIs touch their panel while running) and code generation (what a
row generates) both read this, so it sits outside both.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Sequence
from dataclasses import dataclass

from ._data import data_dir

_PATH = data_dir() / "control_properties.json"


@dataclass(frozen=True)
class ControlProperty:
    """One listed property: ``name`` is NI's long name, ``member`` the Python name
    (the generated property enum's member is its upper-case form), ``value`` the
    kind written (``bool``, ``enum`` or ``number``) and ``doc_url`` NI's page."""

    name: str
    member: str
    value: str
    doc_url: str


@dataclass(frozen=True)
class _Table:
    inherited: dict[int, ControlProperty]
    classes: dict[str, dict[int, ControlProperty]]
    inherited_values: frozenset[int]
    class_values: dict[str, frozenset[int]]


def _entries(raw: dict[str, dict[str, str]]) -> dict[int, ControlProperty]:
    return {
        int(code): ControlProperty(
            entry["name"], entry["member"], entry["value"], entry["doc_url"]
        )
        for code, entry in raw.items()
    }


@functools.cache
def _table() -> _Table:
    with open(_PATH, encoding="utf-8") as handle:
        raw = json.load(handle)
    values = raw["value_codes"]
    return _Table(
        inherited=_entries(raw["inherited"]),
        classes={name: _entries(entries) for name, entries in raw["classes"].items()},
        inherited_values=frozenset(values["inherited"]),
        class_values={
            name: frozenset(codes) for name, codes in values["classes"].items()
        },
    )


def control_property(class_name: str, codes: Sequence[int]) -> ControlProperty | None:
    """The listed property that the row with property ``codes`` (one per path
    segment) of a Property Node bound to a control of class ``class_name`` is;
    None for a nested property or one that is not listed."""
    if len(codes) != 1:
        return None
    table = _table()
    return table.classes.get(class_name, {}).get(codes[0]) or table.inherited.get(
        codes[0]
    )


def is_value_row(class_name: str, codes: Sequence[int]) -> bool:
    """Whether the row with property ``codes`` is the control's ``Value``."""
    if len(codes) != 1:
        return False
    table = _table()
    return (
        codes[0] in table.inherited_values
        or codes[0] in table.class_values.get(class_name, frozenset())
    )
