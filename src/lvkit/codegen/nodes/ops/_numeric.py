"""Numeric type predicates shared by op handlers (scalars and arrays alike)."""

from __future__ import annotations

from lvkit.models import LVType, LVTypeKind

_FLOAT = frozenset({"NumFloat32", "NumFloat64", "NumFloatExt"})
_INTEGER = frozenset(
    {
        "NumInt8",
        "NumInt16",
        "NumInt32",
        "NumInt64",
        "NumUInt8",
        "NumUInt16",
        "NumUInt32",
        "NumUInt64",
    }
)


def _scalar_underlying(lv_type: LVType | None) -> str | None:
    while lv_type is not None and lv_type.kind == LVTypeKind.ARRAY:
        lv_type = lv_type.element_type
    if lv_type is None or lv_type.kind != LVTypeKind.PRIMITIVE:
        return None
    return lv_type.underlying_type


def is_float(lv_type: LVType | None) -> bool:
    """An IEEE floating-point numeric (SGL/DBL/EXT), or an array of one."""
    return _scalar_underlying(lv_type) in _FLOAT


def is_real(lv_type: LVType | None) -> bool:
    """A non-complex numeric (integer or floating-point), or an array of one."""
    underlying = _scalar_underlying(lv_type)
    return underlying in _FLOAT or underlying in _INTEGER
