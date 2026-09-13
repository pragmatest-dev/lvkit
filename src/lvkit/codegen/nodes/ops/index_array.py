"""Index Array (node class ``aIndx``).

LabVIEW's Index Array returns the array element's DEFAULT value for an
out-of-range index — it never errors — whereas a plain Python subscript raises
``IndexError``. So a scalar-element (1D) index is lowered to a guarded
expression carrying the element type's real default; a byte value indexing a
short lookup table (e.g. OpenG Trim Whitespace's 33-entry table) then yields the
default instead of crashing.

Array-element (multi-dimensional) indexing is left as a plain subscript: the
expandable machinery composes the dimensions, and a per-dimension default would
be an empty inner array that the next subscript can't safely index.

Terminals (from the JSON entry):
  in_0 = array
  out index 1 = element
  in_2 = index
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import default_value_expr
from . import register_op


@register_op("INDEX_ARRAY")
def index_array(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    out = next((t for t in node.terminals if t.direction == "output"), None)
    elem = out.lv_type if out else None
    if elem is not None and elem.kind == LVTypeKind.ARRAY:
        # Multi-dimensional index: leave a plain subscript; the expandable
        # machinery composes the dimensions and a per-dimension default would be
        # an empty inner array the next subscript can't safely index.
        return "in_0[int(in_2)]"
    # 1D: return the element's real default on out-of-range (LabVIEW semantics)
    # via the runtime helper, so the array expression is evaluated once.
    default = ast.unparse(default_value_expr(elem))
    return f"_lv.index_array(in_0, in_2, {default})"
