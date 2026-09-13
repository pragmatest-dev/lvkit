"""Delete From Array (node class ``aDelete``).

The "deleted portion" output is polymorphic: deleting a single element (``length``
unwired) yields that ELEMENT as a scalar, while deleting a run (``length`` wired)
yields a SUBARRAY. The static template always sliced ``array[index:index+length]``,
so a single-element delete produced a 1-element list — which an auto-indexing
loop tunnel then accumulated into a 2-D array instead of a flat one. Branch on
the resolved output type instead.

Terminals (from the node_types entry):
  out index 0 = deleted portion
  in  index 1 = array
  in  index 2 = length (default 1)
  out index 3 = output array
  in  index 4 = index (default -1)
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import default_value_expr
from . import register_op


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
    if dt is not None and dt.kind == LVTypeKind.ARRAY:
        deleted_expr = "array[index:index + length]"
    else:
        default = ast.unparse(default_value_expr(dt))
        deleted_expr = f"_lv.index_array(array, index, {default})"
    return {
        # Dict order mirrors output-index order (0 then 3) — the ordinal the
        # dict-hint pairing uses.
        "deleted portion": deleted_expr,
        "output array": "array[:index] + array[index + length:]",
    }
