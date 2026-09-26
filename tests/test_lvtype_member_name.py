"""``LVType.member_name`` / ``item_for``: an enum/ring ordinal as its item."""

from __future__ import annotations

from lvkit.models import EnumValue, LVType, LVTypeKind

_ENUM = LVType(LVTypeKind.ENUM, values={"Read": EnumValue(0), "Write": EnumValue(1)})


def test_member_name_finds_the_item_by_value() -> None:
    assert _ENUM.member_name(1) == "Write"
    assert _ENUM.member_name(7) is None


def test_member_name_without_items_is_none() -> None:
    assert LVType(LVTypeKind.PRIMITIVE, underlying_type="I32").member_name(0) is None


def test_item_for_names_the_item_a_recorded_default_points_at() -> None:
    assert _ENUM.item_for("1") == "Write"
    assert _ENUM.item_for(0) == "Read"
    assert _ENUM.item_for("9") is None  # no such item
    assert _ENUM.item_for("abc") is None  # not an ordinal
    assert _ENUM.item_for(1.5) is None  # a float is not an ordinal
    assert _ENUM.item_for(True) is None  # nor is a bool
    assert _ENUM.item_for(None) is None
