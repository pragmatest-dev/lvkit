"""The To <integer> conversions (To Byte / Word / Long / Quad Integer and their
unsigned forms), for real numbers and arrays of them.

NI, "To Long Integer Function" (labview-api-ref, ``functions/to-long-integer.html``):
"Converts a number to a 32-bit integer in the range -(2^31) to (2^31)-1. This
function rounds all floating-point and fixed-point numeric values to the nearest
integer." "If the fractional part of the floating-point or fixed-point value is .5,
the function rounds the value to the nearest even integer. For example, the
function rounds 13.5 to 14 and rounds 14.5 to 14." ``round`` rounds halves to even.

NI, "Numeric Conversion" (labview, ``numeric-conversion.html``): "Floating-point
number to signed or unsigned integer -- LabVIEW coerces out-of-range values to the
minimum or maximum value of the integer." "Integer to integer -- LabVIEW does not
coerce out-of-range values to the minimum or maximum value of an integer. ... If the
source is larger than the destination, LabVIEW copies only the least significant
bits of the value."

So a floating-point value is compared with the integer's range before it is
rounded (+infinity is the maximum, -infinity the minimum; ``round`` of infinity
raises), and an integer keeps its low bits. NI documents nothing for NaN; it is
left to ``round``, which raises.

One op serves every width: the range comes from the type of the output terminal
in the catalog entry. Terminals: in 1 = number; out 0 = the integer.
"""

from __future__ import annotations

from lvkit.codegen.timestamp_templates import timestamp_template
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.lv_type_codes import INTEGER_BYTE_WIDTHS, SIGNED_INTEGER_TYPE_NAMES
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op
from ._numeric import _scalar_underlying, is_float, is_real
from ._rounding import number_input, over_array_rank, saturating_round


@register_op("TO_INTEGER")
def to_integer(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> str | dict[str, str]:
    number = number_input(node)
    number_type = number.lv_type if number is not None else None
    if timestamp_template(node) is not None or (
        number_type is not None and number_type.is_timestamp
    ):
        # primitive.py replaces the expression with the shared timestamp
        # template (or raises the conversion's gap); a dict keeps it out of the
        # string form's "=" stripping.
        return {"integer": "in_1"}
    out = next((t for t in resolved.terminals if t.direction == "output"), None)
    width = INTEGER_BYTE_WIDTHS.get(out.type or "") if out is not None else None
    if out is None or width is None:
        raise ConversionGap(
            f"{node.name} has no integer type on its output in the catalog", node
        )
    if any(t.lv_type is not None and not is_real(t.lv_type) for t in node.terminals):
        raise ConversionGap(
            f"{node.name} supports real numbers and arrays of them only", node
        )
    bits = width * 8
    signed = out.type in SIGNED_INTEGER_TYPE_NAMES
    # An input whose type is not recorded takes the range test, which also
    # holds for an integer.
    if is_real(number_type) and not is_float(number_type):
        if _scalar_underlying(number_type) == out.type:
            return "in_1"
        ctype = f"c_{'' if signed else 'u'}int{bits}"
        return over_array_rank(f"ctypes.{ctype}(in_1).value", number)
    return over_array_rank(saturating_round("in_1", bits, signed), number)
