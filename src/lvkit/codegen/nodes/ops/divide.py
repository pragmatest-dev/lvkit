"""Division emission selected from the primitive's resolved numeric types."""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op
from ._numeric import is_float


@register_op("DIVIDE")
def divide(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    # The output carries the coerced result type: a floating-point quotient
    # follows IEEE semantics at zero (signed infinity / NaN), never an exception.
    outputs = [t for t in node.terminals if t.direction == "output"]
    if outputs and all(is_float(t.lv_type) for t in outputs):
        return "_lv.float_divide(in_2, in_1)"
    return "in_2 / in_1"
