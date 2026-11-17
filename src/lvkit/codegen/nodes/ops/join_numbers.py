"""Join Numbers (prim 1171).

NI (docs-be ``functions/join-numbers.html``): ``hi`` and ``lo`` "can be an 8-,
16-, or 32-bit number or an array or cluster of that representation"; ``(hi.lo)``
"is an integer twice the width of hi and lo ... If hi and lo are of different
widths, (hi.lo) is twice the width of the widest number." So the high half moves
up by the width of the widest input, whatever the values are. Each half is masked
to its own width first, as an unsigned integer is.

The widths come from the input terminals' types. An array input joins element by
element (``arrayify``). An input holding a cluster joins field by field through
the paired layout walk (``Layout.map_pairs``): ``hi`` and ``lo`` must have the
same shape (the same arrays and clusters), else it is a gap naming both types.

Terminals: out index 0 = (hi.lo); in index 1 = lo; in index 2 = hi.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.terminal_layout import array_rank
from lvkit.lv_type_codes import INTEGER_BYTE_WIDTHS
from lvkit.models import LVType, LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import parse_expr
from ...elementwise import arrayify
from ...flat import BYTE_ORDER_PREFIX, Functions, Integer, UnsupportedLayout, layout_of
from . import register_context_op
from ._flat_node import add_flat_imports

if TYPE_CHECKING:
    from ...context import CodeGenContext


def _element(lv_type: LVType | None) -> LVType | None:
    while lv_type is not None and lv_type.kind == LVTypeKind.ARRAY:
        lv_type = lv_type.element_type
    return lv_type


def _input(node: PrimitiveNode, index: int) -> LVType | None:
    return next(
        t.lv_type for t in node.terminals if t.direction == "input" and t.index == index
    )


def _holds_cluster(lv_type: LVType | None) -> bool:
    element = _element(lv_type)
    return element is not None and element.kind == LVTypeKind.CLUSTER


def _describe(lv_type: LVType | None) -> str:
    if lv_type is None:
        return "an input of unknown type"
    if lv_type.kind == LVTypeKind.ARRAY:
        return f"{lv_type.dimensions or 1}D array of {_describe(lv_type.element_type)}"
    if lv_type.kind == LVTypeKind.CLUSTER:
        fields = ", ".join(_describe(f.type) for f in lv_type.fields or [])
        return f"cluster ({fields})"
    return lv_type.underlying_type or lv_type.kind.value


def _bytes(node: PrimitiveNode, index: int) -> int:
    element = _element(_input(node, index))
    name = element.underlying_type if element is not None else None
    if name not in INTEGER_BYTE_WIDTHS:
        raise ConversionGap(
            f"Join Numbers of {name or 'an input of unknown type'}: only integers "
            "are joined",
            node,
        )
    return INTEGER_BYTE_WIDTHS[name]


def _join(high: Integer, low: Integer, high_value: str, low_value: str) -> str:
    shift = max(high.width, low.width) * 8
    return (
        f"((({high_value}) & {hex((1 << high.width * 8) - 1)}) << {shift}) "
        f"| (({low_value}) & {hex((1 << low.width * 8) - 1)})"
    )


def _join_clusters(
    node: PrimitiveNode, ctx: CodeGenContext, high: LVType | None, low: LVType | None
) -> str:
    fns = Functions(ctx, BYTE_ORDER_PREFIX[0])
    try:
        expression = layout_of(high).map_pairs(
            layout_of(low), "in_2", "in_1", _join, "join_numbers", None, 0, fns
        )
    except UnsupportedLayout as error:
        raise ConversionGap(
            f"Join Numbers of {_describe(high)} (hi) and {_describe(low)} (lo): hi "
            "and lo must have the same shape of arrays and clusters of integers "
            f"({error})",
            node,
        ) from error
    add_flat_imports(ctx, [ast.Expr(value=parse_expr(expression))])
    return expression


@register_context_op("JOIN_NUMBERS")
def join_numbers(
    node: PrimitiveNode, resolved: ResolvedPrimitive, ctx: CodeGenContext
) -> dict[str, str]:
    high, low = _input(node, 2), _input(node, 1)
    if _holds_cluster(high) or _holds_cluster(low):
        return {"hilo": _join_clusters(node, ctx, high, low)}
    expr = _join(
        Integer("", "", _bytes(node, 2)),
        Integer("", "", _bytes(node, 1)),
        "in_2",
        "in_1",
    )
    ranks = {f"in_{index}": array_rank(_input(node, index)) or 0 for index in (1, 2)}
    ranks = {name: rank for name, rank in ranks.items() if rank}
    if ranks:
        lifted, _ = arrayify(parse_expr(expr), ranks)
        expr = ast.unparse(lifted)
    return {"hilo": expr}
