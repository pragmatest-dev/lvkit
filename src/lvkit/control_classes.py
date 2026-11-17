"""The VI Server class of a front-panel control, and NI's number for each class.

A VI file stores a control by its front-panel object class (``stdNum``,
``stdRing``, ...); LabVIEW's VI Server names the same object by a class (Digital,
Ring, Enum, ...) and the ``ClassID`` property of a reference to it is that class's
number (NI: ``class_id_doc_url`` of ``control_classes.json``, "This integer uniquely
identifies the class of a LabVIEW object"). ``control_classes.json`` holds NI's
published table of numbers and, for each front-panel class lvkit maps, the VI Server
class it is with the evidence for the row. A front-panel class that has no row has no
VI Server class here.

The graph (a control's ``vi_server_class``) and code generation (the ``classes=`` of
a generated ``Panel`` and the table of numbers in ``panels.py``) both read this, so
it sits outside both.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass

from ._data import data_dir

_PATH = data_dir() / "control_classes.json"


@dataclass(frozen=True)
class _Row:
    fp_class: str
    type_kind: str | None
    class_name: str


@dataclass(frozen=True)
class _Table:
    class_ids: dict[str, int]
    rows: tuple[_Row, ...]


@functools.cache
def _table() -> _Table:
    with open(_PATH, encoding="utf-8") as handle:
        raw = json.load(handle)
    return _Table(
        class_ids={name: int(number) for name, number in raw["class_ids"].items()},
        rows=tuple(
            _Row(row["fp_class"], row.get("type_kind"), row["class"])
            for row in raw["front_panel_classes"]
        ),
    )


def class_ids() -> dict[str, int]:
    """NI's ClassID number of each VI Server class name it lists."""
    return dict(_table().class_ids)


def vi_server_class(control_type: str | None, type_kind: str | None) -> str | None:
    """The VI Server class of a control of front-panel class ``control_type`` whose
    LabVIEW type is of kind ``type_kind`` (an enum and a ring are both ``stdRing``,
    told apart by it); None for a front-panel class with no row, or whose row needs
    a type kind that is not known."""
    for row in _table().rows:
        if row.fp_class != control_type:
            continue
        if row.type_kind is None or row.type_kind == type_kind:
            return row.class_name
    return None
