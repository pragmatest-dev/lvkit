"""Logarithm Base 10 emission for real numeric scalars and arrays."""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from ..base import CodeGenError
from . import register_op
from ._numeric import is_real


@register_op("LOG10")
def log10(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    if not node.terminals or not all(is_real(term.lv_type) for term in node.terminals):
        raise CodeGenError(
            "Logarithm Base 10 supports resolved real numeric scalars and arrays only",
            node,
        )
    return "_lv.log10(in_1)"
