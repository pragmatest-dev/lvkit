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
from lvkit.terminal_default import DefaultBehavior

from . import register_op, register_op_behaviors


@register_op("STRING_SUBSET")
def string_subset(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    # From offset, take `length` chars. NI, "String Subset": "If offset is
    # unwired or less than 0, the function sets offset to 0", and the substring
    # is empty "if the length is less than or equal to 0"; a Python slice would
    # count a negative offset or length from the end of the string. An unwired
    # length (in_1 is None) is the rest of the string. The comparison with None
    # is `==` because a wired constant length would make `<int> is None` a
    # SyntaxWarning.
    return {
        "substring": "in_3[max(in_2, 0):][:None if in_1 == None else max(in_1, 0)]"
    }


# Unwired, ``length`` takes the rest of the string: ``[:None]``.
register_op_behaviors("STRING_SUBSET", {DefaultBehavior.REST: "None"})
