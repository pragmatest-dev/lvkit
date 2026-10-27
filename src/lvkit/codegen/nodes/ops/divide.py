"""Division emission selected from the primitive's resolved numeric types."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.primitive_resolver import ResolvedPrimitive
from lvkit.python_numeric import float_divide

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from . import register_op
from ._numeric import is_float


@register_op("DIVIDE")
def divide(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    # The output carries the coerced result type: a floating-point quotient
    # follows IEEE results at zero (signed infinity / NaN), never an exception;
    # over arrays that holds for every element.
    outputs = [t for t in node.terminals if t.direction == "output"]
    if not (outputs and all(is_float(t.lv_type) for t in outputs)):
        return "in_2 / in_1"
    ranks = {
        f"in_{t.index}": array_rank(t.lv_type) or 0
        for t in node.terminals
        if t.direction == "input" and t.index in (1, 2)
    }
    quotient, _ = arrayify(
        parse_expr(float_divide("in_2", "in_1")),
        {name: rank for name, rank in ranks.items() if rank},
        conditionals=True,
    )
    return ast.unparse(quotient)
