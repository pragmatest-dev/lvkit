"""String Subset (prim 1503).

Returns the substring starting at ``offset`` with the given ``length``. In
LabVIEW an unwired ``length`` means "to the end of the string", and an unwired
``offset`` defaults to 0 (set as the terminal default in the JSON entry).

Terminals (from the JSON entry):
  out index 0 = substring
  in_1 = length (unwired -> to end)
  in_2 = offset (default 0)
  in_3 = string
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("STRING_SUBSET")
def string_subset(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    # length unwired (in_1 is None) -> slice to the end of the string.
    return {"substring": "in_3[in_2:(in_2 + in_1) if in_1 is not None else None]"}
