"""Default-data decode: integer and enum widths come from the type tables."""

from __future__ import annotations

import pytest

from lvkit.models import LVType, LVTypeKind
from lvkit.parser.vi import _decode_element

DATA = bytes(range(1, 17))


def _t(name: str, kind: LVTypeKind = LVTypeKind.PRIMITIVE) -> LVType:
    return LVType(kind=kind, underlying_type=name)


@pytest.mark.parametrize(
    ("name", "width"),
    [
        ("NumInt8", 1),
        ("NumUInt8", 1),
        ("NumInt16", 2),
        ("NumUInt16", 2),
        ("NumInt32", 4),
        ("NumUInt32", 4),
        ("NumInt64", 8),
        ("NumUInt64", 8),
        ("UnitUInt8", 1),
        ("UnitUInt16", 2),
        ("UnitUInt32", 4),
    ],
)
def test_width_from_type_name(name: str, width: int) -> None:
    kind = LVTypeKind.ENUM if name.startswith("Unit") else LVTypeKind.PRIMITIVE
    value, consumed, _ = _decode_element(DATA, _t(name, kind))
    assert consumed == width
    assert value is not None


def test_unknown_enum_name_is_not_decoded() -> None:
    assert _decode_element(DATA, _t("Enum", LVTypeKind.ENUM)) == (None, 0, None)
