"""The item of NI's ``Data Type.ctl`` enum that each type-descriptor code is.

``data/variant_type_items.json`` lists, for each type-descriptor code name (a
``TD_FULL_TYPE`` member), the label of the enum item the Type output of
``Get Type Information.vi`` returns for it, with the sources of the table. A code
that names several items by itself is not listed. The same file lists the item of
the ``Refnum Type`` enum each refnum kind is (``refnum_type_values``).
"""

from __future__ import annotations

import json
from functools import lru_cache

from ._data import data_dir
from .lv_type_codes import REFNUM_TYPE, TD_FULL_TYPE
from .models import LVType, LVTypeKind


@lru_cache(maxsize=1)
def _labels() -> dict[str, str]:
    path = data_dir() / "variant_type_items.json"
    items: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))["items"]
    unknown = sorted(set(items) - set(TD_FULL_TYPE.__members__))
    if unknown:
        raise ValueError(f"{path.name} names no type-descriptor code {unknown}")
    return items


@lru_cache(maxsize=1)
def _representations() -> dict[str, str]:
    path = data_dir() / "variant_type_items.json"
    items: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))[
        "representations"
    ]
    unknown = sorted(set(items) - set(TD_FULL_TYPE.__members__))
    if unknown:
        raise ValueError(f"{path.name} names no type-descriptor code {unknown}")
    return items


def refnum_type_values(reference_type: LVType) -> dict[tuple[str, str | None], int]:
    """The value of the item of the enum ``reference_type`` (``Refnum Type``) that
    each refnum is, by ``(refnum kind, VI Server class)`` as a generated variant's
    ``LVType`` records them (the class is None for every kind but a generic
    reference), for the refnums the table lists whose item the enum holds; empty
    when ``reference_type`` is not an enum."""
    if reference_type.kind != LVTypeKind.ENUM or not reference_type.values:
        return {}
    path = data_dir() / "variant_type_items.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(data["refnum_types"]) - set(REFNUM_TYPE.__members__))
    if unknown:
        raise ValueError(f"{path.name} names no refnum kind {unknown}")
    values = reference_type.values
    table: dict[tuple[str, str | None], int] = {
        (kind, None): values[label].value
        for kind, label in sorted(data["refnum_types"].items())
        if label in values
    }
    for server_class, label in sorted(data["refnum_server_classes"].items()):
        if label in values:
            table[("LVObjCtl", server_class)] = values[label].value
    return table


def representation_values(representation: LVType) -> dict[str, int] | None:
    """The value of the item of the enum ``representation`` each number type (by its
    type-descriptor code name) is, for the names the table lists whose item the
    enum holds; None when ``representation`` is not an enum."""
    if representation.kind != LVTypeKind.ENUM or not representation.values:
        return None
    return {
        name: representation.values[label].value
        for name, label in sorted(_representations().items())
        if label in representation.values
    }


def item_values_by_code(data_type: LVType) -> dict[int, int] | None:
    """The value of the item of the enum ``data_type`` each type-descriptor code is,
    for the codes the table lists whose item the enum holds; None when
    ``data_type`` is not an enum."""
    if data_type.kind != LVTypeKind.ENUM or not data_type.values:
        return None
    return {
        int(TD_FULL_TYPE[name].value): data_type.values[label].value
        for name, label in sorted(_labels().items())
        if label in data_type.values
    }
