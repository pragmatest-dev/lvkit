"""Quotient & Remainder (prim 1056).

LabVIEW's Q&R uses floored division, matching Python's ``//`` and ``%`` (both
floor toward negative infinity), so the identity x == y*(x//y) + (x%y) holds.

Two outputs, mapped by position: out index 0 = floor(x/y), out index 1 = x%y.
in_3 = x, in_2 = y.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("QUOTIENT_REMAINDER")
def quotient_remainder(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    return {"quotient": "in_3 // in_2", "remainder": "in_3 % in_2"}
