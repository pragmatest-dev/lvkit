"""Delete From Array (node class ``aDelete``).

The "deleted portion" output is polymorphic: deleting a single element (``length``
unwired) yields that ELEMENT as a scalar, so an auto-indexing loop tunnel
accumulates a flat array; deleting a run (``length`` wired) yields a SUBARRAY. The
output type the node resolves to decides which.

Terminals (from the node_types entry):
  out index 0 = deleted portion
  in  index 1 = array
  in  index 2 = length (unwired: one element)
  out index 3 = output array
  in  index 4 = index (unwired: the last ``length`` elements); an array of
      several dimensions has one index per dimension

An array of several dimensions deletes along ONE dimension, the one whose index
is wired (NI: "you can wire only one index input"; none wired is the first
dimension, its last element). The rows, columns or pages deleted are cut from
every row above that dimension (``_generate_nd_delete``).
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive
from lvkit.terminal_default import DefaultBehavior

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...native_array import dimension_sizes, guarded_index
from ...output_binding import bind_output
from ...type_default import type_default
from ..terminal_roles import laid_out_roles
from . import register_context_op, register_op_behaviors, register_op_fragment


def _along(held: str, depth: int, along: int, cut: Callable[[str], str]) -> str:
    """``cut`` applied to ``held`` at dimension ``along``, inside one
    comprehension per dimension above it."""
    if depth == along:
        return cut(held)
    row = f"_row{depth}"
    return f"[{_along(row, depth + 1, along, cut)} for {row} in {held}]"


@register_op_fragment("DELETE_FROM_ARRAY")
def _generate_nd_delete(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    """Delete From Array of an array of several dimensions. The run
    ``[index, index + length)`` of the wired index's dimension, clipped to it, is
    the deleted portion (a ``length`` that is wired) and what is left is the
    output array; with ``length`` unwired one row, column or page goes and the
    deleted portion has one dimension less. An index outside the dimension
    deletes nothing and gives an empty deleted portion. Arrays of one dimension
    are the scalar path (``delete_from_array``)."""
    roles = laid_out_roles(ctx, node, "aDelete") if node.terminals else {}
    array_term = roles[1][0][1] if 1 in roles else None
    if (
        array_term is None
        or array_term.lv_type is None
        or array_term.lv_type.kind != LVTypeKind.ARRAY
        or (array_term.lv_type.dimensions or 1) < 2
    ):
        return None
    rank = array_term.lv_type.dimensions or 1
    deleted_term = roles[0][0][1]
    output_term = roles[3][0][1]
    wired = [
        (placed.dimension, text)
        for placed, term in roles.get(4, [])
        if (text := ctx.wired_input(term.id, node)) is not None
    ]
    if len(wired) > 1:
        raise ConversionGap(
            "Delete From Array with more than one index wired is not converted "
            "(NI: you can wire only one index input)",
            node,
        )
    along, index = wired[0] if wired else (0, None)
    wanted = [t for t in (deleted_term, output_term) if ctx.is_wired(t.id)]
    if not wanted:
        return CodeFragment.empty()
    source = ctx.wired_input(array_term.id, node) or "[]"
    array = ctx.make_output_var("source_array", node.id)
    size = dimension_sizes(array, rank)[along]
    length = ctx.wired_input(roles[2][0][1].id, node)
    count = length if length is not None else "1"
    start = ctx.make_output_var("delete_start", node.id)
    stop = ctx.make_output_var("delete_stop", node.id)
    statements: list[ast.stmt] = [build_assign(array, parse_expr(source))]
    if length is None:
        # one row, column or page: a position outside the dimension deletes none
        statements.append(
            build_assign(
                start,
                parse_expr(
                    f"({index} if 0 <= {index} < {size} else {size})"
                    if index is not None
                    else f"max({size} - 1, 0)"
                ),
            )
        )
        statements.append(build_assign(stop, parse_expr(f"{start} + 1")))
    else:
        first = f"max({index}, 0)" if index is not None else f"max({size} - {count}, 0)"
        statements.append(build_assign(start, parse_expr(first)))
        statements.append(
            build_assign(
                stop,
                parse_expr(
                    f"max({start} + {count}, {start}, 0)"
                    if index is None
                    else f"max({index} + {count}, {index}, 0)"
                ),
            )
        )
    fragment = CodeFragment(statements=statements)
    if ctx.is_wired(deleted_term.id):
        if length is None:
            taken = _along(array, 0, along, lambda held: f"{held}[{start}]")
            deleted = f"({taken} if {start} < {size} else [])"
        else:
            deleted = _along(array, 0, along, lambda held: f"{held}[{start}:{stop}]")
        fragment += bind_output(
            node,
            deleted_term.id,
            "deleted_portion",
            parse_expr(deleted),
            ctx,
            movable=False,
        )
    if ctx.is_wired(output_term.id):
        rest = _along(
            array, 0, along, lambda held: f"{held}[:{start}] + {held}[{stop}:]"
        )
        fragment += bind_output(
            node, output_term.id, "output_array", parse_expr(rest), ctx, movable=False
        )
    return fragment


@register_context_op("DELETE_FROM_ARRAY")
def delete_from_array(
    node: PrimitiveNode, resolved: ResolvedPrimitive, ctx: CodeGenContext
) -> dict[str, str]:
    deleted = next(
        (t for t in node.terminals if t.direction == "output" and t.index == 0),
        None,
    )
    dt = deleted.lv_type if deleted else None
    # A subarray delete (length wired) keeps the slice; a single-element delete
    # returns the ELEMENT so an auto-indexing tunnel builds a flat array. Like
    # Index Array, an out-of-range single delete yields the element DEFAULT (the
    # array shrinks each pass, so a stale index must not raise) — the slice form
    # silently returned [], scalar subscript would raise IndexError instead.
    # The run deleted is ``[index, index + length)`` clipped to the array (NI: a
    # negative index with a wired length deletes ``length - |index|`` elements,
    # none when ``length`` is smaller than ``|index|``); a Python slice would
    # count a negative bound from the end instead.
    start = "max(index, 0)"
    stop = "max(index + length, index, 0)"
    if dt is not None and dt.kind == LVTypeKind.ARRAY:
        deleted_expr = f"array[{start}:{stop}]"
    else:
        default = ast.unparse(type_default(ctx, dt))
        deleted_expr = guarded_index("array", "index", default)
    return {
        # Dict order mirrors output-index order (0 then 3) — the ordinal the
        # dict-hint pairing uses.
        "deleted portion": deleted_expr,
        "output array": f"array[:{start}] + array[{stop}:]",
    }


# Unwired, ``length`` deletes one element and ``index`` starts ``length`` elements
# from the end of the array (``in_1``), at least at the first element: the last
# ``length`` elements go, all of them when ``length`` exceeds the array.
register_op_behaviors(
    "DELETE_FROM_ARRAY",
    {
        DefaultBehavior.ONE_ELEMENT: "1",
        DefaultBehavior.LAST_ELEMENT: "max(len(in_1) - in_2, 0)",
    },
)
