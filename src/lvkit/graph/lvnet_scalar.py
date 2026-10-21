"""Typed scalar values for the lvnet text surface.

The parser decodes a control's saved default and a diagram constant's value as
text (``"True"``, ``"-1"``, ``"0.5"``). lvnet spells a Boolean as ``True`` /
``False`` and a number bare, and a string quoted, so a value read back from
its type is the one spelling the text can round-trip.
"""

from __future__ import annotations

from ..models import LVType, LVTypeKind, ScalarValue

_INTEGER_UNDERLYING_TYPES = frozenset(
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
_FLOAT_UNDERLYING_TYPES = frozenset(
    {"NumFloat32", "NumFloat64", "NumFloatExt", "UnitFloat32", "UnitFloat64"}
)


def lvnet_typed_scalar(value: ScalarValue, lv_type: LVType | None) -> ScalarValue:
    """``value`` in the Python type its LabVIEW type names: a Boolean's
    ``"True"``/``"False"`` as a ``bool``, an integer / enum / ring ordinal as an
    ``int``, a float as a ``float``. A value that is not text, a type that is not
    resolved, and text the type cannot read as its own number are returned
    unchanged, so nothing is guessed."""
    if lv_type is None or not isinstance(value, str):
        return value
    underlying = lv_type.underlying_type or ""
    if lv_type.kind == LVTypeKind.PRIMITIVE and underlying == "Boolean":
        if value == "True":
            return True
        if value == "False":
            return False
        return value
    is_integer = lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING) or (
        lv_type.kind == LVTypeKind.PRIMITIVE
        and underlying in _INTEGER_UNDERLYING_TYPES
    )
    if is_integer:
        try:
            return int(value)
        except ValueError:
            return value
    if lv_type.kind == LVTypeKind.PRIMITIVE and underlying in _FLOAT_UNDERLYING_TYPES:
        try:
            return float(value)
        except ValueError:
            return value
    return value
