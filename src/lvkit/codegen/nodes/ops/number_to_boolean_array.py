"""Number To Boolean Array (primResID 1814).

Produces one boolean per bit of the input integer, LSB first. The array LENGTH
is the input's integer TYPE width (8/16/32/64) -- NOT the value's significant-bit
count. A static template using ``in_1.bit_length()`` under-sizes the array for
any value with leading zero bits (a U32 whose top bits are 0 still yields 32
booleans), so read the width from the input type and bake it into the range.

Terminals (from the primitives.json entry):
  in  index 1 = number (its int type sets the array length)
  out index 0 = boolean array
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op

_INT_WIDTH = {
    "NumInt8": 8,
    "NumUInt8": 8,
    "NumInt16": 16,
    "NumUInt16": 16,
    "NumInt32": 32,
    "NumUInt32": 32,
    "NumInt64": 64,
    "NumUInt64": 64,
}


@register_op("NUMBER_TO_BOOLEAN_ARRAY")
def number_to_boolean_array(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    number = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 1),
        None,
    )
    lt = number.lv_type if number else None
    underlying = (lt.underlying_type or "") if lt else ""
    width = _INT_WIDTH.get(underlying, 32)
    return {"boolean array": f"[bool((in_1 >> i) & 1) for i in range({width})]"}
