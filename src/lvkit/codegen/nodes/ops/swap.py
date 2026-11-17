"""Swap Bytes (prim 1162) and Swap Words (prim 1163).

Both act on an integer, an array of integers or a cluster, and "swap only the
integer elements of the cluster" (NI): how many bytes each integer has comes from
its type, so the code is written for the type of the data input (index 1; the
output, index 0, has the data's type). Swap Bytes swaps the two bytes of every
16-bit word; Swap Words swaps the two 16-bit halves of every 32-bit long.
"""

from __future__ import annotations

import ast

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
from ._flat_node import add_flat_imports, terminal, unsupported_layout_error


def _swap_leaf(fns: Functions, chunk: int, width_min: int) -> IntegerLeaf:
    """The integer with its ``chunk``-byte halves swapped, when it has at least
    ``width_min`` bytes."""

    def leaf(integer: Integer, value: str) -> str:
        if integer.width < width_min:
            return value
        signed = integer.code.islower()
        name = fns.named(
            f"swap_{'bytes' if chunk == 1 else 'words'}",
            integer.ctype,
            (integer.ctype,),
            lambda n: (
                f"def {n}(value):\n"
                f"    raw = ctypes.{integer.ctype}(value).value.to_bytes("
                f"{integer.width}, 'big', signed={signed})\n"
                f"    swapped = b''.join(raw[i + {chunk}:i + {2 * chunk}] + "
                f"raw[i:i + {chunk}] for i in range(0, {integer.width}, {2 * chunk}))\n"
                f"    return int.from_bytes(swapped, 'big', signed={signed})\n"
            ),
        )
        return f"{name}({value})"

    return leaf


def _swap(
    node: PrimitiveNode, ctx: CodeGenContext, chunk: int, width_min: int
) -> CodeFragment:
    source = terminal(node, "input", 1)
    result = terminal(node, "output", 0)
    if result is None or not ctx.is_wired(result.id):
        return CodeFragment.empty()
    value = (ctx.wired_input(source.id, node) if source else None) or "None"
    try:
        layout = layout_of(source.lv_type if source else None)
    except UnsupportedLayout as error:
        raise unsupported_layout_error(node, error) from error
    fns = Functions(ctx, BYTE_ORDER_PREFIX[0])
    kind = f"swap_{'bytes' if chunk == 1 else 'words'}"
    expression = layout.map_integers(
        value, _swap_leaf(fns, chunk, width_min), kind, width_min, 0, fns
    )
    swapped = parse_expr(expression)
    add_flat_imports(ctx, [ast.Expr(value=swapped)])
    return bind_output(node, result.id, "swapped", swapped, ctx)


@register_op_fragment("SWAP_BYTES")
def swap_bytes(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    return _swap(node, ctx, 1, 2)


@register_op_fragment("SWAP_WORDS")
def swap_words(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    return _swap(node, ctx, 2, 4)
