"""Round Toward -Infinity (1075) and Round Toward +Infinity (1076) for real numbers
and arrays of them.

NI, "Round Toward -Infinity Function" (labview-api-ref,
``functions/round-toward-infinity.html``): "Truncates the input to the next lowest
integer. For example, if the input is 3.8, the result is 3. If the input is -3.8,
the result is -4." "Round Toward +Infinity Function" (``round-toward-infinity-2.html``):
"Rounds the input to the next highest integer. For example, if the input is 3.1, the
result is 4. If the input is -3.1, the result is -3." Both take x as "a scalar
number, array or cluster of numbers" and give the resulting integer value in the
same form: a floating-point input gives a floating-point whole number (positive or
negative infinity, which has no next integer, stays as it is) and an integer is
already whole. NI documents nothing for NaN; it is left to ``math.floor`` /
``math.ceil``, which raise.

Terminals (from the primitives entries): in 1 = x; out 0 = the rounded value.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op
from ._numeric import is_float, is_real
from ._rounding import number_input, over_array_rank


def _rounded(node: PrimitiveNode, function: str) -> str:
    number = number_input(node)
    number_type = number.lv_type if number is not None else None
    if is_real(number_type) and not is_float(number_type):
        return "in_1"
    whole = f"math.{function}(in_1)"
    if is_float(number_type):
        whole = f"float({whole})"
    # An input whose type is not recorded keeps the value of the function, and
    # infinity is still returned as it is.
    return over_array_rank(f"(in_1 if math.isinf(in_1) else {whole})", number)


@register_op("ROUND_TOWARD_NEGATIVE_INFINITY")
def round_toward_negative_infinity(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> str:
    return _rounded(node, "floor")


@register_op("ROUND_TOWARD_POSITIVE_INFINITY")
def round_toward_positive_infinity(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> str:
    return _rounded(node, "ceil")
