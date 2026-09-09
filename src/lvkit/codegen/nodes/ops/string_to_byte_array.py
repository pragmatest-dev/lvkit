"""String To Byte Array (prim 1608).

A LabVIEW string is a byte string; "String To Byte Array" yields the array of
its unsigned bytes. latin-1 is the faithful 1:1 map of a byte string to 0..255,
so ``list(s.encode("latin-1"))`` reproduces the bytes exactly.

Terminals (from the JSON entry): in_1 = string (index 1) -> out index 0.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("STRING_TO_BYTE_ARRAY")
def string_to_byte_array(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    return {"byte_array": "list(in_1.encode('latin-1'))"}
