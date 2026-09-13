"""Type Cast (primResID 1166).

LabVIEW's Type Cast reinterprets the FLAT bytes of ``x`` as the type of a
prototype input — general flat-format (de)serialization. This handler emits
``_lv.type_cast(x, src, dst)`` with type specs read from the pane: the SOURCE
spec from the data input (index 0) and the DEST spec from the output (index 2,
whose type mirrors the type-prototype input at index 1).

The runtime supports the well-defined numeric cases — strings, scalar integers,
and 1-D integer arrays (big-endian, no length prefix) — which cover the
byte<->word reinterprets in the MD5 / binary-string VIs. Other type pairs
(clusters, variants, floats) produce a spec the runtime rejects with a loud
NotImplementedError rather than emitting silently-wrong bytes; they await a
fuller flat-format implementation.

Terminals (from the primitives.json entry):
  in  index 0 = x (data to reinterpret)
  in  index 1 = type (prototype; its type is the output type)
  out index 2 = x cast to the prototype's type
"""

from __future__ import annotations

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from . import register_op

_INT_SPEC = {
    "NumInt8": "i8", "NumUInt8": "u8",
    "NumInt16": "i16", "NumUInt16": "u16",
    "NumInt32": "i32", "NumUInt32": "u32",
    "NumInt64": "i64", "NumUInt64": "u64",
}


def _spec(lt: LVType | None) -> str:
    """A `_lv.type_cast` type spec for a terminal type. Supported: 'str', scalar
    ints, and 1-D integer arrays ('u32[]', …). Anything else yields a descriptive
    spec the runtime rejects loudly (never a silently-wrong cast)."""
    if lt is None:
        return "unknown"
    if lt.kind == LVTypeKind.ARRAY:
        elem = getattr(lt.element_type, "underlying_type", None)
        return f"{_INT_SPEC[elem]}[]" if elem in _INT_SPEC else "array"
    underlying = lt.underlying_type or ""
    if underlying == "String":
        return "str"
    return _INT_SPEC.get(underlying, underlying or "unknown")


@register_op("TYPE_CAST")
def type_cast(node: PrimitiveNode, resolved: ResolvedPrimitive) -> str:
    def _term(direction: str, index: int) -> LVType | None:
        for t in node.terminals:
            if t.direction == direction and t.index == index:
                return t.lv_type
        return None

    src_spec = _spec(_term("input", 0))
    dst_spec = _spec(_term("output", 2))
    return f"_lv.type_cast(in_0, {src_spec!r}, {dst_spec!r})"
