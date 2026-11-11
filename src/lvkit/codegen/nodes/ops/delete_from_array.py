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
  in  index 4 = index (unwired: the last ``length`` elements)
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive
from lvkit.terminal_default import DefaultBehavior

from ...ast_utils import type_default_expr
from ...native_array import guarded_index
from . import register_op, register_op_behaviors


@register_op("DELETE_FROM_ARRAY")
def delete_from_array(
    node: PrimitiveNode, resolved: ResolvedPrimitive
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
        default = ast.unparse(type_default_expr(dt))
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
