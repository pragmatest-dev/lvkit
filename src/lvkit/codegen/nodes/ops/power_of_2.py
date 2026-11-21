"""Power Of 2 (primResID 1223) for real scalars and arrays.

NI, "Power Of 2 Function" (labview-api-ref, ``functions/power-of-2.html``):
"Computes 2 raised to the x power", and "2^x is of the same numeric
representation as x". So an integer x gives an integer 2^x (a Python ``int``, as
a ``range`` bound or an index needs), and a floating-point x a floating-point one.

* Floating point: a result past the largest value of the type is infinity, as in
  LabVIEW; ``2.0 ** x`` alone raises ``OverflowError`` there.
* Integer: LabVIEW integer arithmetic wraps without an error (NI forum, "Why No
  Rollover for While Loop i I32": "7fff ffff is the biggest positive number a I32
  can have, anything bigger turns into a negative number"), so 2^x is taken to
  the width of the type: 0 for x at or past the width of an unsigned type, and
  for x equal to the top bit of a signed one its minimum value. NI does not state
  the result for a negative integer x: 2^x is then a fraction below 1, which the
  integer representation rounds to 0 (recorded default).

A complex input has no code generation yet.

Terminals (from the primitives entry): in 1 = x; out 0 = 2^x.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.lv_type_codes import INTEGER_BYTE_WIDTHS, SIGNED_INTEGER_TYPE_NAMES
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from . import register_op
from ._numeric import _scalar_underlying, is_float, is_real

# 2 ** x overflows a double from x = 1024.
_FLOAT = "(math.inf if in_1 >= 1024 else 2.0 ** in_1)"


def _integer(underlying: str) -> str:
    bits = INTEGER_BYTE_WIDTHS[underlying] * 8
    power = f"((1 << in_1) if 0 <= in_1 < {bits} else 0)"
    mask = (1 << bits) - 1
    if underlying in SIGNED_INTEGER_TYPE_NAMES:
        sign = 1 << (bits - 1)
        return f"(({power} & {mask} ^ {sign}) - {sign})"
    return f"({power} & {mask})"


@register_op("POWER_OF_2")
def power_of_2(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    out = next((t for t in node.terminals if t.direction == "output"), None)
    inp = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 1), None
    )
    if out is None or inp is None or not is_real(out.lv_type):
        raise ConversionGap(
            "Power Of 2 supports real numbers and arrays of them only", node
        )
    if is_float(out.lv_type):
        expression = _FLOAT
    else:
        underlying = _scalar_underlying(out.lv_type)
        assert underlying is not None
        expression = _integer(underlying)
    rank = array_rank(inp.lv_type) or 0
    if not rank:
        return expression
    power, _ = arrayify(parse_expr(expression), {"in_1": rank}, conditionals=True)
    return ast.unparse(power)
