"""Logarithm Base 10 emission for real numeric scalars and arrays."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from . import register_op
from ._numeric import is_real

# Zero gives negative infinity and a negative number NaN, as for the Formula Node
# ``log``; ``math.log10`` alone would raise.
_LOG10 = "(math.log10(in_1) if in_1 > 0 else (-math.inf if in_1 == 0 else math.nan))"


@register_op("LOG10")
def log10(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    if not node.terminals or not all(is_real(term.lv_type) for term in node.terminals):
        raise ConversionGap(
            "Logarithm Base 10 supports resolved real numeric scalars and arrays only",
            node,
        )
    rank = max(
        (
            array_rank(t.lv_type) or 0
            for t in node.terminals
            if t.direction == "input" and t.index == 1
        ),
        default=0,
    )
    if not rank:
        return _LOG10
    logarithm, _ = arrayify(parse_expr(_LOG10), {"in_1": rank}, conditionals=True)
    return ast.unparse(logarithm)
