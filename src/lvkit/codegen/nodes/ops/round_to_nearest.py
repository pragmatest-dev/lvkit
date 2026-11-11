"""Round To Nearest (primResID 1055) for numeric scalars and arrays.

NI, "Round To Nearest Function" (labview-api-ref, ``functions/round-to-nearest.html``):
"Rounds the input to the nearest integer. If the value of the input is midway
between two integers, the function returns the nearest even integer. For example,
if number is 1.5 or 2.5, nearest integer value is 2." The result is "of the same
data type structure as number": a floating-point number stays floating-point (a
whole number), an integer is already whole. Python's ``round`` rounds half to even
as well. Infinity and NaN, which ``round`` rejects, are returned as they are (they
have no nearest integer). A time stamp, a complex number or a fixed-point number
has no code generation yet.

Terminals (from the primitives entry): in 1 = number; out 0 = nearest integer
value.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from . import register_op
from ._numeric import is_float, is_real

_FLOAT = "(float(round(in_1)) if math.isfinite(in_1) else in_1)"


@register_op("ROUND_TO_NEAREST")
def round_to_nearest(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    if not node.terminals or not all(is_real(term.lv_type) for term in node.terminals):
        raise ConversionGap(
            "Round To Nearest supports real numbers and arrays of them only", node
        )
    number = next(t for t in node.terminals if t.direction == "input" and t.index == 1)
    if not is_float(number.lv_type):
        # An integer is already its own nearest integer.
        return "in_1"
    rank = array_rank(number.lv_type) or 0
    if not rank:
        return _FLOAT
    rounded, _ = arrayify(parse_expr(_FLOAT), {"in_1": rank}, conditionals=True)
    return ast.unparse(rounded)
