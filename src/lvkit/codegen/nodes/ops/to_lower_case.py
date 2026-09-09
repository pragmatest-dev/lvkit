"""To Lower Case (prim 1189). in_1 = string -> out index 0 = lowercased."""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("TO_LOWER_CASE")
def to_lower_case(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    return {"lower": "in_1.lower()"}
