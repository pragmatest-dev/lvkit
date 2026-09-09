"""Max & Min (prim 1108).

Two outputs: the smaller then the larger of the two inputs. The dict template's
keys map to the wired output terminals by position, so ``minimum`` (output
index 0) precedes ``maximum`` (output index 1).

Terminals (from the JSON entry):
  out index 0 = min(x, y)
  out index 1 = max(x, y)
  in_2 = y
  in_3 = x
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op


@register_op("MAX_MIN")
def max_min(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    return {"minimum": "min(in_3, in_2)", "maximum": "max(in_3, in_2)"}
