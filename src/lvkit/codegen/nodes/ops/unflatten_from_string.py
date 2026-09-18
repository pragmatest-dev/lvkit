"""Unflatten From String (primResID 1165).

LabVIEW's Unflatten From String parses a flattened binary string (as produced
by Flatten To String) back into the type wired to the ``type`` prototype
input — general flat-format deserialization, the inverse counterpart to
Type Cast (1166) but with LENGTH-PREFIX and byte-order handling Type Cast
doesn't have (Type Cast is a fixed-width, no-prefix reinterpret; Unflatten
From String's String/array targets carry a 4-byte size prefix by default,
matching Flatten To String's own output format).

This handler emits ``_lv.unflatten_from_string(binary_string, dst, byte_order,
includes_size)`` with the DEST spec read from the pane the same way Type Cast
reads it: from the "value" output (index 2), whose type mirrors the
type-prototype input (index 7) — confirmed across ~15 real corpus instances
spanning Array/Cluster/Refnum/6 numeric types, all varying together at those
two indices while index 8/0 stay fixed as the error cluster.

The runtime supports the same well-defined cases as Type Cast (strings,
scalar integers, 1-D integer arrays); other type pairs (clusters, refnums,
floats -- which DO appear in this corpus, e.g. Strip Units__ogtk.vi's
NumFloat32/64/Ext/Complex* variants) raise a loud NotImplementedError rather
than emit silently-wrong bytes, same policy as Type Cast.

Terminals (from the primitives.json entry):
  in  index  7 = type (prototype; its type is the output "value" type)
  in  index  8 = error in (Cluster -- error cluster; Python uses exceptions)
  in  index  9 = byte order (UnitUInt16 enum: 0=big-endian, 1=native, 2=little)
  in  index 10 = data includes array or string size? (Boolean; default True)
  in  index 11 = binary string
  out index  0 = error out (Cluster -- error cluster; Python uses exceptions)
  out index  2 = value (unflattened data, matches the type-prototype's type)
  out index  3 = rest of the binary string
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op
from .type_cast import _spec


@register_op("UNFLATTEN_FROM_STRING")
def unflatten_from_string(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    def _term(direction: str, index: int):
        return next(
            (
                t
                for t in node.terminals
                if t.direction == direction and t.index == index
            ),
            None,
        )

    value_term = _term("output", 2)
    dst_spec = _spec(value_term.lv_type if value_term else None)
    call = f"_lv.unflatten_from_string(in_11, {dst_spec!r}, in_9, in_10)"
    return {"value": f"{call}[0]", "rest_of_string": f"{call}[1]"}
