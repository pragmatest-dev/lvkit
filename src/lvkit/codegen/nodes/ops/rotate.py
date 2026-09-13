"""Rotate (primResID 1082).

LabVIEW's Rotate rotates the bits of ``x`` left by ``y`` (``y < 0`` rotates
right), modulo the integer WIDTH of ``x`` — so the emission needs that width,
which a static template can't express. Read it from the value input's integer
type and defer the wrap-around arithmetic to ``_lv.rotate``.

Terminals (from the primitives.json entry):
  in  index 1 = x (value; its int type sets the rotation width)
  in  index 2 = y (bit count; signed — >0 left, <0 right)
  out index 0 = x rotated
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op

_INT_WIDTH = {
    "NumInt8": 8,
    "NumUInt8": 8,
    "NumInt16": 16,
    "NumUInt16": 16,
    "NumInt32": 32,
    "NumUInt32": 32,
    "NumInt64": 64,
    "NumUInt64": 64,
}


@register_op("ROTATE")
def rotate(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    value = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 1),
        None,
    )
    lt = value.lv_type if value else None
    underlying = (lt.underlying_type or "") if lt else ""
    width = _INT_WIDTH.get(underlying, 32)
    return f"_lv.rotate(in_1, in_2, {width})"
