"""Byte Array To String (prim 1609).

Inverse of String To Byte Array: an array of unsigned bytes back to the byte
string. latin-1 maps 0..255 to the matching code points 1:1.

in_1 = unsigned byte array -> out index 0 = string.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("BYTE_ARRAY_TO_STRING")
def byte_array_to_string(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    return {"string": "bytes(in_1).decode('latin-1')"}
