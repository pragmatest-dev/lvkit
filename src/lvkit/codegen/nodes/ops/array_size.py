"""Array Size (primResID 1809).

LabVIEW's Array Size returns the element COUNT of a 1-D array, but for a 2-D+
array it returns a 1-D vector of per-dimension sizes ``[d0, d1, ...]`` — a shape
that depends on the input's dimensionality, which a static template can't
express. Read the dimension count from the input array type: 1-D stays a clean
``len``; 2-D+ is a list of the length along each axis.

Terminals (from the primitives.json entry):
  in  index 1 = array
  out index 0 = size (scalar count for 1-D; size vector for 2-D+)
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from ...native_array import size_vector
from . import register_op


@register_op("ARRAY_SIZE")
def array_size(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    array = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 1),
        None,
    )
    lt = array.lv_type if array else None
    return size_vector("in_1", getattr(lt, "dimensions", None) or 1)
