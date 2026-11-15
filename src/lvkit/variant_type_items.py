"""The item of NI's ``Data Type.ctl`` enum that each type-descriptor code is.

``data/variant_type_items.json`` lists, for each type-descriptor code name (a
``TD_FULL_TYPE`` member), the label of the enum item the Type output of
``Get Type Information.vi`` returns for it, with the sources of the table. A code
that names several items by itself is not listed.
"""

from __future__ import annotations

import json
from functools import lru_cache

from ._data import data_dir
from .lv_type_codes import TD_FULL_TYPE
from .models import LVType, LVTypeKind


@lru_cache(maxsize=1)
def _labels() -> dict[str, str]:
    path = data_dir() / "variant_type_items.json"
    items: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))["items"]
    unknown = sorted(set(items) - set(TD_FULL_TYPE.__members__))
    if unknown:
        raise ValueError(f"{path.name} names no type-descriptor code {unknown}")
    return items


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
