"""Split Number (prim 1170).

NI (docs-be ``functions/split-number.html``): ``x`` "can be an 8-, 16-, 32-, or
64-bit integer, or an array or cluster of those representations"; ``hi(x)`` and
``lo(x)`` "are integers that are half the width of x ... 8-bit, 16-bit, or 32-bit
unsigned integers"; ``hi(x)`` is the high-order half and ``lo(x)`` the low-order
half, whatever the endianness. "If you wire an 8-bit integer input, this function
does not split the number. Instead, LabVIEW returns the same 8-bit number in the
lo(x) output, and returns zero for the hi(x) output."

The halves depend on the width of the input's integer type, which a static
template cannot express, so it is read from the input terminal. An array or
cluster input is a gap.

Terminals: in index 0 = x; out index 1 = hi(x), out index 2 = lo(x).
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive
from lvkit.python_numeric import CTYPES_INTEGER

from . import register_op


@register_op("SPLIT_NUMBER")
def split_number(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    source = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 0),
        None,
    )
    lv_type = source.lv_type if source else None
    underlying = (lv_type.underlying_type or "") if lv_type else ""
    if underlying not in CTYPES_INTEGER:
        raise ConversionGap(
            f"Split Number of {underlying or 'an input of unknown type'}: only an "
            "integer is split here (an array or cluster of integers is not)"
        )
    width = CTYPES_INTEGER[underlying][1]
    if width == 8:
        return {"high": "0", "low": "in_0"}
    half = width // 2
    mask = hex((1 << half) - 1)
    return {"high": f"(in_0 >> {half}) & {mask}", "low": f"in_0 & {mask}"}
