"""The value a front-panel control's settable properties have when the VI starts,
read from the control's ``ddo``.

Each property is read from the field the heap keeps it in, and the result is keyed by
the property's member name (``control_properties.json``): ``tip_strip`` (every
control), ``doc_max`` (a scrollbar), ``x_min`` / ``x_max`` / ``y_min`` / ``y_max``
(a graph), ``index_values`` (an array), ``item_names`` and ``active_cell`` (a
listbox). A property whose field the file leaves out gets the value the absent field
means (a graph's scale minimum is 0 when ``scaleDMin`` is absent). Format facts and
the corpus evidence: ``docs/_internal/design/labview-binary-format.md``,
"A control's settable properties start from fields of its ddo".
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from typing import Any

from .utils import int_or_none, strip_surrounding_quotes

# The text pylabview prints for a string with no bytes.
_EMPTY_STRING_TEXT = "[NULL]"

# The partID of the horizontal and of the vertical scale in a graph's ``partsList``
# (the heap's ``GraphScaleData`` pairs them the same way), with the members of each.
_SCALE_MEMBERS = {18: ("x_min", "x_max"), 19: ("y_min", "y_max")}

_HEX_DOUBLE = re.compile(r"[0-9A-Fa-f]{16}")


def read_starting_properties(ddo: ET.Element, control_type: str) -> dict[str, Any]:
    """The starting value of each property the control ``ddo`` of front-panel class
    ``control_type`` has and the conversion decodes, by member name."""
    found: dict[str, Any] = {"tip_strip": tip_strip(ddo)}
    if control_type == "scrollbar":
        _put(found, "doc_max", ddo.find("scrollbarMax"))
    if control_type == "indArr":
        found["index_values"] = [
            int_or_none(item.text) or 0
            for item in ddo.findall("arrayIndices/SL__arrayElement")
        ]
    if control_type == "listbox":
        _listbox(found, ddo)
    if control_type == "stdGraph":
        for part in ddo.findall("partsList/SL__arrayElement[@class='scale']"):
            members = _SCALE_MEMBERS.get(int_or_none(part.findtext("partID")) or 0)
            if members is not None:
                _put(found, members[0], part.find("scaleDMin"))
                _put(found, members[1], part.find("scaleDMax"))
    return found


def _put(found: dict[str, Any], member: str, element: ET.Element | None) -> None:
    """Record the double in ``element`` as ``member``: 0 for a field the file leaves
    out, nothing for one stored in a form other than 16 hex digits (big-endian
    IEEE 754), which stays a property whose starting value is not decoded."""
    text = (element.text or "").strip() if element is not None else ""
    if not text:
        found[member] = 0.0
    elif _HEX_DOUBLE.fullmatch(text):
        found[member] = float(struct.unpack(">d", bytes.fromhex(text))[0])


def tip_strip(ddo: ET.Element) -> str:
    """A control's tip strip: the second string of its ``description`` (the first is
    the description); "" when the description holds one string or the control has
    none."""
    strings = ddo.findall("description/strings/SL__arrayElement")
    return _string(strings[1].text) if len(strings) > 1 else ""


def _string(text: str | None) -> str:
    raw = (text or "").strip()
    return "" if raw == _EMPTY_STRING_TEXT else strip_surrounding_quotes(raw)


def _listbox(found: dict[str, Any], ddo: ET.Element) -> None:
    """A listbox's item strings (one list per row: ``baseListboxItemStrings`` is the
    ``[rows, columns]`` sizes and the strings row-major) and its active cell
    ``(row, column)``."""
    sizes = [
        int_or_none(item.text) or 0
        for item in ddo.findall(
            "baseListboxItemStrings/multiDimArraySizes/SL__arrayElement"
        )
    ]
    items = [
        _string(item.text)
        for item in ddo.findall(
            "baseListboxItemStrings/multiDimArrayElems/SL__arrayElement"
        )
    ]
    if len(sizes) == 2 and sizes[0] * sizes[1] == len(items):
        rows, columns = sizes
        found["item_names"] = [
            items[row * columns : (row + 1) * columns] for row in range(rows)
        ]
    cell = ddo.find("activeCell")
    if cell is not None:
        found["active_cell"] = (
            int_or_none(cell.findtext("row")) or 0,
            int_or_none(cell.findtext("col")) or 0,
        )
