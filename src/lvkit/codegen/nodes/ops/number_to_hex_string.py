"""Number To Hexadecimal String (prim 1181).

NI: the function is polymorphic -- a number gives a string, an array of numbers
an array of strings. ``width`` (index 1) pads each string with leading zeros;
``number`` is index 2 and the string output index 0. Whether the input is an
array is read from its terminal type.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("NUMBER_TO_HEX_STRING")
def number_to_hex_string(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    number = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 2), None
    )
    if number is not None and number.lv_type is not None:
        if number.lv_type.kind == LVTypeKind.ARRAY:
            return {"hex_string": "['%0*X' % (in_1, b) for b in in_2]"}
    return {"hex_string": "'%0*X' % (in_1, in_2)"}
