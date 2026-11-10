"""Power Of 10 (primResID 1212) for floating-point scalars and arrays.

NI, "Power Of 10 Function" (labview-api-ref, ``functions/power-of-10.html``):
"Computes 10 raised to the x power", of "the same numeric representation as x".
A floating-point number past the largest value of its type is infinity, as in
LabVIEW; ``10.0 ** x`` alone raises ``OverflowError`` there. An integer or complex
input has no code generation yet.

Terminals (from the primitives entry): in 1 = x; out 0 = 10^x.
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
from ._numeric import is_float

# 10 ** x overflows a double from log10 of the largest double (about 308.2547).
_POWER = "(math.inf if in_1 >= 308.25471555991675 else 10.0 ** in_1)"


@register_op("POWER_OF_10")
def power_of_10(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    if not node.terminals or not all(is_float(term.lv_type) for term in node.terminals):
        raise ConversionGap(
            "Power Of 10 supports floating-point numbers and arrays of them only",
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
        return _POWER
    power, _ = arrayify(parse_expr(_POWER), {"in_1": rank}, conditionals=True)
    return ast.unparse(power)
