"""Join Numbers (prim 1171).

NI (docs-be ``functions/join-numbers.html``): ``hi`` and ``lo`` "can be an 8-,
16-, or 32-bit number or an array or cluster of that representation"; ``(hi.lo)``
"is an integer twice the width of hi and lo ... If hi and lo are of different
widths, (hi.lo) is twice the width of the widest number." So the high half moves
up by the width of the widest input, whatever the values are. Each half is masked
to its own width first, as an unsigned integer is.

The widths come from the input terminals' types; an array input joins element by
element (``arrayify``). A cluster input has no code generation yet.

Terminals: out index 0 = (hi.lo); in index 1 = lo; in index 2 = hi.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.lv_type_codes import INTEGER_BYTE_WIDTHS
from lvkit.models import LVType, LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from . import register_op


def _element(lv_type: LVType | None) -> LVType | None:
    while lv_type is not None and lv_type.kind == LVTypeKind.ARRAY:
        lv_type = lv_type.element_type
    return lv_type


def _bytes(node: PrimitiveNode, index: int) -> int:
    term = next(
        t for t in node.terminals if t.direction == "input" and t.index == index
    )
    element = _element(term.lv_type)
    name = element.underlying_type if element is not None else None
    if element is None or element.kind == LVTypeKind.CLUSTER:
        raise ConversionGap(
            "Join Numbers of a cluster has no code generation yet", node
        )
    if name not in INTEGER_BYTE_WIDTHS:
        raise ConversionGap(
            f"Join Numbers of {name or 'an input of unknown type'}: only integers "
            "are joined",
            node,
        )
    return INTEGER_BYTE_WIDTHS[name]


@register_op("JOIN_NUMBERS")
def join_numbers(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    lo_bytes = _bytes(node, 1)
    hi_bytes = _bytes(node, 2)
    shift = max(lo_bytes, hi_bytes) * 8
    expr = (
        f"((in_2 & {hex((1 << hi_bytes * 8) - 1)}) << {shift}) "
        f"| (in_1 & {hex((1 << lo_bytes * 8) - 1)})"
    )
    ranks = {
        f"in_{t.index}": array_rank(t.lv_type) or 0
        for t in node.terminals
        if t.direction == "input" and t.index in (1, 2)
    }
    ranks = {name: rank for name, rank in ranks.items() if rank}
    if ranks:
        lifted, _ = arrayify(parse_expr(expr), ranks)
        expr = ast.unparse(lifted)
    return {"hilo": expr}
