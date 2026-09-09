"""Reverse String (prim 1537). in_1 = string -> out index 0 = reversed."""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("REVERSE_STRING")
def reverse_string(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    return {"reversed": "in_1[::-1]"}
