"""To Unsigned Long Integer / U32 (prim 1145).

LabVIEW's numeric->integer coercion rounds to nearest (round-half-to-even, which
Python's round() also does), then the U32 representation wraps modulo 2**32.

in_1 = number -> out index 0 = unsigned 32-bit integer.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("TO_U32")
def to_u32(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    return {"u32": "int(round(in_1)) & 0xFFFFFFFF"}
