"""Numeric type predicates shared by op handlers (scalars and arrays alike)."""

from __future__ import annotations

from typing import NamedTuple

from lvkit.lv_type_codes import (
    COMPLEX_TYPE_NAMES,
    INTEGER_BYTE_WIDTHS,
    SIGNED_INTEGER_TYPE_NAMES,
    TD_FULL_TYPE,
    is_boolean_type_name,
    is_float_type_name,
    is_string_type_name,
)
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


def is_array(lv_type: LVType | None) -> bool:
    return lv_type is not None and lv_type.kind == LVTypeKind.ARRAY


class FormatKind(NamedTuple):
    """How Format Into String sees an input: its kind (``string``, ``path``,
    ``bool``, ``int``, ``float``, ``complex``, ``enum``, or the name of a type it
    has no conversion for), for an integer its bit width and signedness, and for
    an enum each item's value and label."""

    kind: str
    bits: int = 0
    signed: bool = False
    labels: tuple[tuple[int, str], ...] = ()


def format_kind(lv_type: LVType | None) -> FormatKind:
    """The ``FormatKind`` of a wired input of type ``lv_type`` (``unknown`` when
    the VI records no type for it)."""
    if lv_type is None:
        return FormatKind("unknown")
    if lv_type.kind == LVTypeKind.ENUM:
        if not lv_type.values:
            return FormatKind("enum with no item labels")
        return FormatKind(
            "enum",
            labels=tuple(
                sorted((item.value, label) for label, item in lv_type.values.items())
            ),
        )
    # A ring is an integer whose values have labels: it is written as that integer.
    if lv_type.kind not in (LVTypeKind.PRIMITIVE, LVTypeKind.RING):
        return FormatKind(lv_type.kind.value)
    name = lv_type.underlying_type
    if name in INTEGER_BYTE_WIDTHS:
        return FormatKind(
            "int", INTEGER_BYTE_WIDTHS[name] * 8, name in SIGNED_INTEGER_TYPE_NAMES
        )
    if name in COMPLEX_TYPE_NAMES:
        return FormatKind("complex")
    if is_float_type_name(name):
        return FormatKind("float")
    if is_boolean_type_name(name):
        return FormatKind("bool")
    if is_string_type_name(name):
        return FormatKind("string")
    if name == TD_FULL_TYPE.Path.name:
        return FormatKind("path")
    return FormatKind(name or "unknown")
