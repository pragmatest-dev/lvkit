"""Array Subset (node class ``subset``).

Terminals (the ``subset`` entry in ``_meta.json``): input 0 = array, output 1 =
subarray, then per dimension an ``index`` input (an unwired index starts at 0) and
a ``length`` input (an unwired length runs to the end of the dimension).

A one-dimensional array is one slice, ``array[index:index + length]``; each
further dimension nests a comprehension that slices the rows. Python slicing
clips both ends to the array, as the function does.
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...native_array import operand
from ..terminal_roles import laid_out_roles
from . import register_op_fragment


def _slice(start: str | None, length: str | None) -> str:
    begin = operand(start) if start else ""
    if length is None:
        return f"{begin}:"
    if not start:
        return f":{operand(length)}"
    return f"{begin}:{begin} + {operand(length)}"


def _subset(array: str, starts: list[str | None], lengths: list[str | None]) -> str:
    """``array`` sliced along its first dimension, each row sliced by the rest."""
    head = f"{array}[{_slice(starts[0], lengths[0])}]"
    if len(starts) == 1:
        return head
    row = "_row" if len(starts) == 2 else f"_row{len(starts)}"
    inner = _subset(row, starts[1:], lengths[1:])
    return f"[{inner} for {row} in {head}]"


@register_op_fragment("ARRAY_SUBSET")
def array_subset(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    roles = laid_out_roles(node, "subset")
    array_term = roles[0][0][1]
    output_term = roles[1][0][1]
    array = ctx.resolve(array_term.id) or "[]"
    starts = [ctx.resolve(term.id) for _, term in roles.get(2, [])]
    lengths = [ctx.resolve(term.id) for _, term in roles.get(3, [])]
    count = max(len(starts), len(lengths), 1)
    starts += [None] * (count - len(starts))
    lengths += [None] * (count - len(lengths))
    if not ctx.is_wired(output_term.id):
        return CodeFragment.empty()
    variable = ctx.make_output_var("subarray", node.id, terminal_id=output_term.id)
    return CodeFragment(
        statements=[
            build_assign(variable, parse_expr(_subset(array, starts, lengths)))
        ],
        bindings={output_term.id: variable},
    )
