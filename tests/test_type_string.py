"""The type string (a type descriptor as 16-bit integers): the exact words NI's
"Type Descriptors" page gives, and the round trip through ``decode``."""

from __future__ import annotations

import pytest

from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind
from lvkit.type_string import TypeStringError, decode, encode
from tests.native_vi import array, scalar

I16 = scalar("NumInt16")
U32 = scalar("NumUInt32")
I32 = scalar("NumInt32")
DBL = scalar("NumFloat64")
BOOL = scalar("Boolean")
STR = scalar("String")


def words(*hex_words: int) -> list[int]:
    return [w - 0x10000 if w & 0x8000 else w for w in hex_words]


def cluster(*members: LVType) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name=f"m{i}", type=t) for i, t in enumerate(members)],
    )


def enum(*labels: str) -> LVType:
    return LVType(
        kind=LVTypeKind.ENUM,
        underlying_type="UnitUInt8",
        values={label: EnumValue(value=i) for i, label in enumerate(labels)},
    )


def test_a_number_is_a_four_byte_descriptor() -> None:
    assert encode(I32) == words(0x0004, 0x0003)
    assert encode(DBL) == words(0x0004, 0x000A)
    assert encode(BOOL) == words(0x0004, 0x0021)


def test_a_string_is_variable_sized() -> None:
    assert encode(STR) == words(0x0008, 0x0030, 0xFFFF, 0xFFFF)


def test_arrays_hold_their_element_inline() -> None:
    assert encode(array("NumFloat64")) == words(
        0x000E, 0x0040, 0x0001, 0xFFFF, 0xFFFF, 0x0004, 0x000A
    )
    assert encode(array("Boolean", 2)) == words(
        0x0012, 0x0040, 0x0002, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0x0004, 0x0021
    )


def test_a_cluster_holds_its_members_inline() -> None:
    assert encode(cluster(I16, U32)) == words(
        0x000E, 0x0050, 0x0002, 0x0004, 0x0002, 0x0004, 0x0007
    )


def test_an_enum_lists_its_items() -> None:
    expected = words(
        0x0016, 0x0015, 0x0003, 0x0261, 0x6D02, 0x666D, 0x0966, 0x6D20,
        0x7374, 0x6572, 0x656F,
    )  # fmt: skip
    assert encode(enum("am", "fm", "fm stereo")) == expected


@pytest.mark.parametrize(
    "lv_type",
    [
        I32,
        DBL,
        BOOL,
        STR,
        array("NumInt32"),
        array("Boolean", 2),
        cluster(STR, I16),
        cluster(array("NumInt16"), cluster(DBL, BOOL)),
        array(cluster(STR, I16)),
    ],
)
def test_decode_reads_back_what_encode_wrote(lv_type: LVType) -> None:
    decoded = decode(encode(lv_type))
    assert encode(decoded) == encode(lv_type)
    assert decoded.kind == lv_type.kind
    assert decoded.underlying_type in (lv_type.underlying_type, "Cluster", "Array")


def test_decode_reads_a_cluster_of_a_string_and_an_i16() -> None:
    decoded = decode(encode(cluster(STR, I16)))
    assert decoded.kind == LVTypeKind.CLUSTER
    assert [f.type.underlying_type for f in decoded.fields or []] == [
        "String",
        "NumInt16",
    ]


def test_decode_reads_the_enum_items() -> None:
    decoded = decode(encode(enum("am", "fm", "fm stereo")))
    assert decoded.kind == LVTypeKind.ENUM
    assert list(decoded.values or {}) == ["am", "fm", "fm stereo"]


def test_a_named_descriptor_is_read() -> None:
    # NI's example: an I16 named "radio" (size 4 + 6, flag 0x40 on the code word).
    assert decode(words(0x000A, 0x4002, 0x0572, 0x6164, 0x696F)).underlying_type == (
        "NumInt16"
    )


@pytest.mark.parametrize(
    "numbers",
    [
        [],
        words(0x0004),
        words(0x0008, 0x0003),
        words(0x0004, 0x0003, 0x0004, 0x0003),
        words(0x0004, 0x00FE),
    ],
)
def test_decode_refuses_what_is_not_a_descriptor(numbers: list[int]) -> None:
    with pytest.raises(TypeStringError):
        decode(numbers)


def test_a_type_with_no_layout_has_no_type_string() -> None:
    with pytest.raises(TypeStringError):
        encode(scalar("Path"))
    with pytest.raises(TypeStringError):
        encode(array(array("NumInt8")))
