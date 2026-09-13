"""Spreadsheet String To Array (prim 1539).

The output array's DIMENSIONALITY and element type — not a wire — decide how the
string is split: a 2-D array type splits rows on the EOL and columns on the
delimiter, while a 1-D array type treats the delimiter and an EOL alike as
element separators. Those come from the output terminal's type, so this is
emitted as a logic op rather than a static template. Per-field conversion
(str/int/float) follows the element type.

Terminals (from the JSON entry):
  in_1 = delimiter
  in_3 = spreadsheet string
  out index 0 = array
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


def _element_kind(array_type: LVType | None) -> str:
    """Map the array's element type to a runtime conversion kind."""
    elem = getattr(array_type, "element_type", None)
    underlying = getattr(elem, "underlying_type", None) or ""
    if underlying.startswith(("NumFloat", "NumComplex")):
        return "float"
    if underlying.startswith(("NumInt", "NumUInt")):
        return "int"
    return "str"


@register_op("SPREADSHEET_STRING_TO_ARRAY")
def spreadsheet_string_to_array(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> str:
    out = next((t for t in node.terminals if t.direction == "output"), None)
    out_type = out.lv_type if out else None
    ndims = getattr(out_type, "dimensions", None) or 1
    elem = _element_kind(out_type)
    return f"_lv.spreadsheet_string_to_array(in_3, in_1, {ndims}, {elem!r})"
