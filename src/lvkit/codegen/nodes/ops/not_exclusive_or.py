"""Not Exclusive Or (prim 1073), boolean form.

NOT(x XOR y) is boolean equality: True exactly when x and y agree. Terminals
here are Boolean (in_1 = y, in_2 = x -> out index 0).
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("NOT_EXCLUSIVE_OR")
def not_exclusive_or(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    return {"result": "in_2 == in_1"}
