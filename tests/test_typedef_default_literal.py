"""``default_literal``: the one rule every surface shares for a recorded default."""

from __future__ import annotations

from lvkit.graph.typedef import TypedefField, default_literal, field_default_literal
from lvkit.models import LVType, LVTypeKind

_STRING = LVType(LVTypeKind.PRIMITIVE, underlying_type="String")
_DBL = LVType(LVTypeKind.PRIMITIVE, underlying_type="DBL")
_ENUM = LVType(LVTypeKind.ENUM)


def test_only_a_string_is_quoted() -> None:
    assert default_literal("x", _STRING) == '"x"'
    assert default_literal("3.0", _DBL) == "3.0"
    assert default_literal("Write", _ENUM) == "Write"  # an item name, not a string
    assert default_literal(None, _STRING) is None


def test_a_surface_supplies_its_own_quoting() -> None:
    assert default_literal("x", _STRING, quote=lambda s: f"<{s}>") == "<x>"
    # a non-string value is spelled by `word` (identity by default), not by `quote`
    assert default_literal("x", _DBL, quote=lambda s: f"<{s}>") == "x"
    # a number stays bare; any other non-string value goes through `word`
    word = lambda s: f"[{s}]"  # noqa: E731
    assert default_literal("3.0", _DBL, word=word) == "3.0"
    assert default_literal("Read, Write", _ENUM, word=word) == "[Read, Write]"


def test_saved_elements_follow_the_element_types_rule() -> None:
    strings = LVType(LVTypeKind.ARRAY, element_type=_STRING)
    numbers = LVType(LVTypeKind.ARRAY, element_type=_DBL)
    assert default_literal(None, strings, ("a", "b")) == '["a", "b"]'
    assert default_literal(None, numbers, ("1.0", "2.0")) == "[1.0, 2.0]"


def test_field_default_literal_uses_the_fields_type_and_elements() -> None:
    steps = LVType(LVTypeKind.ARRAY, element_type=_DBL)
    f = TypedefField("Steps", steps, None, (), ("1",))
    assert field_default_literal(f) == "[1]"
    assert field_default_literal(TypedefField("N", _DBL, None, ())) is None
