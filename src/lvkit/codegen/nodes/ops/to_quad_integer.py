"""To Quad Integer (primResID 1155) for real numbers and arrays of them.

NI, "To Quad Integer Function" (labview-api-ref, ``functions/to-quad-integer.html``):
"Converts a number to a 64-bit integer in the range -(2^63) to (2^63)-1." "This
function rounds all floating-point and fixed-point numeric values to the nearest
integer. If the fractional part of the value is .5, the function rounds the value
to the nearest even integer. For example, the function rounds 13.5 to 14 and
rounds 14.5 to 14." ``round`` rounds halves to even; a value outside the range is
the nearest end of it, as To Long Integer's is. A time stamp or complex number has
no code generation yet.

Terminals (from the primitives entry): in 1 = number; out 0 = 64bit integer.
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
from ._numeric import is_real

_ROUNDED = "int(round(in_1))"
_QUAD = (
    f"(-9223372036854775808 if {_ROUNDED} < -9223372036854775808 else "
    f"9223372036854775807 if {_ROUNDED} > 9223372036854775807 else {_ROUNDED})"
)


@register_op("TO_QUAD_INTEGER")
def to_quad_integer(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    if not node.terminals or not all(is_real(t.lv_type) for t in node.terminals):
        raise ConversionGap(
            "To Quad Integer supports real numbers and arrays of them only", node
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
        return _QUAD
    quad, _ = arrayify(parse_expr(_QUAD), {"in_1": rank}, conditionals=True)
    return ast.unparse(quad)
