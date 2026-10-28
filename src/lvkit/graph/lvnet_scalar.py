"""Typed scalar values for the lvnet text surface.

The parser decodes a control's saved default and a diagram constant's value as
text (``"True"``, ``"-1"``, ``"0.5"``). lvnet spells a Boolean as ``True`` /
``False`` and a number bare, and a string quoted, so a value read back from
its type is the one spelling the text can round-trip.
"""

from __future__ import annotations

from ..models import LVType, LVTypeKind, ScalarValue
from ..string_literal import quote_string

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


def lvnet_literal_token(value: ScalarValue) -> str:
    """THE single lvnet §4/§10 literal-value TOKEN renderer for a raw
    ``ScalarValue`` -- a connector-pane control's own authored default
    (``ConnectorPaneTerminal.default``), a wired-constant driver, or a
    labeled ``constant`` node's own value (see ``_lvnet_const_value_str``,
    which feeds the last two through this for their plain-scalar case).
    A string is escaped (md §4/§10), so a control character (a CRLF) or a
    ``"`` in its text never spans a physical line of the line-oriented grammar.

    A ``str`` renders double-quoted with standard backslash escapes
    (``\\\\``, ``\\"``, ``\\n``, ``\\r``, ``\\t`` -- ``string_literal.STRING_ESCAPES``);
    any OTHER C0 control char (U+0000-U+001F) as ``\\xHH``. A ``bool`` checks
    FIRST (Python's ``bool`` is an ``int`` subclass) -> ``True``/``False``;
    ``int``/``float`` -> plain ``str()``. Never called with ``None``.
    """
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, str):
        return quote_string(value)
    return str(value)
