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

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...flat import BYTE_ORDER_PREFIX, Functions, UnsupportedLayout, layout_of
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import add_flat_imports, terminal, unsupported_fragment


def _swap(
    node: PrimitiveNode, ctx: CodeGenContext, chunk: int, width_min: int
) -> CodeFragment:
    source = terminal(node, "input", 1)
    result = terminal(node, "output", 0)
    if result is None or not ctx.is_wired(result.id):
        return CodeFragment.empty()
    value = (ctx.resolve(source.id) if source else None) or "None"
    try:
        layout = layout_of(source.lv_type if source else None)
    except UnsupportedLayout as error:
        return unsupported_fragment(node, ctx, str(error))
    fns = Functions(ctx, BYTE_ORDER_PREFIX[0])
    expression = layout.swap(value, chunk, width_min, 0, fns)
    variable = ctx.make_output_var("swapped", node.id, terminal_id=result.id)
    statements: list[ast.stmt] = [build_assign(variable, parse_expr(expression))]
    add_flat_imports(ctx, statements)
    return CodeFragment(statements=statements, bindings={result.id: variable})


@register_op_fragment("SWAP_BYTES")
def swap_bytes(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    return _swap(node, ctx, 1, 2)


@register_op_fragment("SWAP_WORDS")
def swap_words(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    return _swap(node, ctx, 2, 4)
