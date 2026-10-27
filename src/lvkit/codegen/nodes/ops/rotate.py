"""Rotate (primResID 1082).

LabVIEW's Rotate rotates the bits of ``x`` left by ``y`` (``y < 0`` rotates
right), modulo the integer WIDTH of ``x`` -- so the emission needs that width,
which a static template can't express. It is read from the value input's integer
type, and the result is wrapped to that width.

Terminals (from the primitives.json entry):
  in  index 1 = x (value; its int type sets the rotation width)
  in  index 2 = y (bit count; signed -- >0 left, <0 right)
  out index 0 = x rotated
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive
from lvkit.python_numeric import CTYPES_INTEGER, fixed_width

from . import register_op


@register_op("ROTATE")
def rotate(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    value = next(
        (t for t in node.terminals if t.direction == "input" and t.index == 1),
        None,
    )
    lt = value.lv_type if value else None
    underlying = (lt.underlying_type or "") if lt else ""
    if underlying not in CTYPES_INTEGER:
        underlying = "NumUInt32"
    width = CTYPES_INTEGER[underlying][1]
    unsigned = {8: "NumUInt8", 16: "NumUInt16", 32: "NumUInt32", 64: "NumUInt64"}[width]
    bits = fixed_width("in_1", unsigned)
    return fixed_width(
        f"({bits} << (in_2 % {width})) | ({bits} >> ({width} - in_2 % {width}))",
        underlying,
    )
