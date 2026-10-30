"""Facts about LabVIEW's type codes, read from pylabview's enums.

A type descriptor names its type by a ``TD_FULL_TYPE`` code and a refnum names its
kind by a ``REFNUM_TYPE`` code. The groups here say which names are the same wire
type stored differently (a Boolean in 8 or 16 bits, a String as a length-prefixed
run, a C string or a Pascal string), so every consumer that decides by type asks
the one group.
"""

from __future__ import annotations

import pylabview.LVdatatype as _lv_datatype  # type: ignore[import-untyped]

TD_FULL_TYPE = _lv_datatype.TD_FULL_TYPE
REFNUM_TYPE = _lv_datatype.REFNUM_TYPE

BOOLEAN_TYPE_NAMES: frozenset[str] = frozenset(
    {TD_FULL_TYPE.Boolean.name, TD_FULL_TYPE.BooleanU16.name}
)

STRING_TYPE_NAMES: frozenset[str] = frozenset(
    {
        TD_FULL_TYPE.String.name,
        TD_FULL_TYPE.String2.name,
        TD_FULL_TYPE.CString.name,
        TD_FULL_TYPE.PasString.name,
        TD_FULL_TYPE.SubString.name,
    }
)

INTEGER_TYPE_NAMES: frozenset[str] = frozenset(
    member.name
    for member in (
        TD_FULL_TYPE.NumInt8,
        TD_FULL_TYPE.NumInt16,
        TD_FULL_TYPE.NumInt32,
        TD_FULL_TYPE.NumInt64,
        TD_FULL_TYPE.NumUInt8,
        TD_FULL_TYPE.NumUInt16,
        TD_FULL_TYPE.NumUInt32,
        TD_FULL_TYPE.NumUInt64,
    )
)

FLOAT_TYPE_NAMES: frozenset[str] = frozenset(
    member.name
    for member in (
        TD_FULL_TYPE.NumFloat32,
        TD_FULL_TYPE.NumFloat64,
        TD_FULL_TYPE.NumFloatExt,
        TD_FULL_TYPE.NumComplex64,
        TD_FULL_TYPE.NumComplex128,
        TD_FULL_TYPE.NumComplexExt,
        TD_FULL_TYPE.UnitFloat32,
        TD_FULL_TYPE.UnitFloat64,
        TD_FULL_TYPE.UnitFloatExt,
        TD_FULL_TYPE.UnitComplex64,
        TD_FULL_TYPE.UnitComplex128,
        TD_FULL_TYPE.UnitComplexExt,
    )
)

# Every number type: integers, floating-point and complex numbers (with or
# without a unit), unsigned integers with a unit, and fixed-point numbers.
NUMERIC_TYPE_NAMES: frozenset[str] = (
    INTEGER_TYPE_NAMES
    | FLOAT_TYPE_NAMES
    | frozenset(
        member.name
        for member in (
            TD_FULL_TYPE.UnitUInt8,
            TD_FULL_TYPE.UnitUInt16,
            TD_FULL_TYPE.UnitUInt32,
            TD_FULL_TYPE.FixedPoint,
            TD_FULL_TYPE.ComplexFixedPt,
        )
    )
)

# The bytes one value of each integer / floating-point type takes when flattened.
INTEGER_BYTE_WIDTHS: dict[str, int] = {
    TD_FULL_TYPE.NumInt8.name: 1,
    TD_FULL_TYPE.NumInt16.name: 2,
    TD_FULL_TYPE.NumInt32.name: 4,
    TD_FULL_TYPE.NumInt64.name: 8,
    TD_FULL_TYPE.NumUInt8.name: 1,
    TD_FULL_TYPE.NumUInt16.name: 2,
    TD_FULL_TYPE.NumUInt32.name: 4,
    TD_FULL_TYPE.NumUInt64.name: 8,
}
SIGNED_INTEGER_TYPE_NAMES: frozenset[str] = frozenset(
    {
        TD_FULL_TYPE.NumInt8.name,
        TD_FULL_TYPE.NumInt16.name,
        TD_FULL_TYPE.NumInt32.name,
        TD_FULL_TYPE.NumInt64.name,
    }
)
FLOAT_BYTE_WIDTHS: dict[str, int] = {
    TD_FULL_TYPE.NumFloat32.name: 4,
    TD_FULL_TYPE.NumFloat64.name: 8,
}


def is_boolean_type_name(type_name: str | None) -> bool:
    """Whether ``type_name`` is a Boolean (stored in 8 or 16 bits)."""
    return type_name in BOOLEAN_TYPE_NAMES


def is_string_type_name(type_name: str | None) -> bool:
    """Whether ``type_name`` is a string (however it is stored)."""
    return type_name in STRING_TYPE_NAMES


def is_numeric_type_name(type_name: str | None) -> bool:
    """Whether ``type_name`` is a number of any kind (integer, floating-point,
    complex, fixed-point, with or without a unit)."""
    return type_name in NUMERIC_TYPE_NAMES


def is_int32_type_name(type_name: str | None) -> bool:
    """Whether ``type_name`` is a signed 32-bit integer."""
    return type_name == TD_FULL_TYPE.NumInt32.name


def is_integer_type_name(type_name: str | None) -> bool:
    """Whether ``type_name`` is a signed or unsigned integer."""
    return type_name in INTEGER_TYPE_NAMES

# A physical quantity (a number with a unit) flattens as the plain number it
# wraps; the unit type's name maps to that number's.
_PLAIN_NUMBER: dict[str, str] = {
    TD_FULL_TYPE.UnitFloat32.name: TD_FULL_TYPE.NumFloat32.name,
    TD_FULL_TYPE.UnitFloat64.name: TD_FULL_TYPE.NumFloat64.name,
    TD_FULL_TYPE.UnitFloatExt.name: TD_FULL_TYPE.NumFloatExt.name,
    TD_FULL_TYPE.UnitComplex64.name: TD_FULL_TYPE.NumComplex64.name,
    TD_FULL_TYPE.UnitComplex128.name: TD_FULL_TYPE.NumComplex128.name,
    TD_FULL_TYPE.UnitComplexExt.name: TD_FULL_TYPE.NumComplexExt.name,
}


def plain_number_name(type_name: str) -> str:
    """The number type a value of ``type_name`` flattens as: the plain number
    inside a unit type, else ``type_name`` itself."""
    return _PLAIN_NUMBER.get(type_name, type_name)


def is_unit_number_name(type_name: str | None) -> bool:
    """Whether ``type_name`` is a floating-point or complex number with a unit."""
    return type_name in _PLAIN_NUMBER


def is_float_type_name(type_name: str | None) -> bool:
    """Whether ``type_name`` is a floating-point or complex number, with or
    without a unit."""
    return type_name in FLOAT_TYPE_NAMES
