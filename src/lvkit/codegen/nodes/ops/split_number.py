"""Split Number (prim 1170).

NI (docs-be ``functions/split-number.html``): ``x`` "can be an 8-, 16-, 32-, or
64-bit integer, or an array or cluster of those representations"; ``hi(x)`` and
``lo(x)`` "are integers that are half the width of x ... 8-bit, 16-bit, or 32-bit
unsigned integers, respectively, or an array or cluster of those representations";
``hi(x)`` is the high-order half and ``lo(x)`` the low-order half, whatever the
endianness. "If you wire an 8-bit integer input, this function does not split the
number. Instead, LabVIEW returns the same 8-bit number in the lo(x) output, and
returns zero for the hi(x) output."

The halves depend on the width of the input's integer type, which a static
template cannot express, so it is read from the input terminal's type: an array
is split element by element (one comprehension per dimension) and a cluster
field by field, each output keeping the input's shape
(``Layout.map_integers``).

Terminals: in index 0 = x; out index 1 = hi(x), out index 2 = lo(x).
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import parse_expr
from ...context import CodeGenContext
from ...flat import (
    BYTE_ORDER_PREFIX,
    Functions,
    Integer,
    IntegerLeaf,
    UnsupportedLayout,
    layout_of,
)
from ...fragment import CodeFragment
from ...output_binding import bind_output
from . import register_op_fragment
from ._flat_node import terminal, unsupported_layout_error


def _half_leaf(high: bool) -> IntegerLeaf:
    def leaf(integer: Integer, value: str) -> str:
        if integer.width == 1:
            return "0" if high else value
        half = integer.width * 4
        mask = hex((1 << half) - 1)
        return f"(({value}) >> {half}) & {mask}" if high else f"({value}) & {mask}"

    return leaf


@register_op_fragment("SPLIT_NUMBER")
def split_number(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    source = terminal(node, "input", 0)
    value = (ctx.wired_input(source.id, node) if source else None) or "None"
    try:
        layout = layout_of(source.lv_type if source else None)
    except UnsupportedLayout as error:
        raise unsupported_layout_error(node, error) from error
    fns = Functions(ctx, BYTE_ORDER_PREFIX[0])
    fragment = CodeFragment.empty()
    for index, name, high in ((1, "high", True), (2, "low", False)):
        output = terminal(node, "output", index)
        if output is None or not ctx.is_wired(output.id):
            continue
        expression = layout.map_integers(
            value, _half_leaf(high), f"split_{name}", name, 0, fns
        )
        parsed = parse_expr(expression)
        fragment.extend(bind_output(node, output.id, name, parsed, ctx))
    return fragment
