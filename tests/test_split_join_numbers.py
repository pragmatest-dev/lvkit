"""Split Number (1170) and Join Numbers (1171) of a scalar, an array, a 2D array
and a cluster of integers: each case is generated on a one-node diagram and run."""

from __future__ import annotations

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.native_vi import array, generate, node, scalar

U8 = scalar("NumUInt8")
U16 = scalar("NumUInt16")
U32 = scalar("NumUInt32")
I16 = scalar("NumInt16")
I32 = scalar("NumInt32")
DBL = scalar("NumFloat64")


def cluster(*fields: tuple[str, LVType]) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name=name, type=type_) for name, type_ in fields],
    )


def split(source: LVType, high: LVType, low: LVType):
    return generate(
        {"x": source},
        {"hi": high, "lo": low},
        [
            node(
                "sp",
                1170,
                [(0, "input", source), (1, "output", high), (2, "output", low)],
            )
        ],
        [("fp:x", "sp:0"), ("sp:1", "fp:hi"), ("sp:2", "fp:lo")],
    ).function


def join(high: LVType, low: LVType, joined: LVType):
    return generate(
        {"hi": high, "lo": low},
        {"r": joined},
        [
            node(
                "jn",
                1171,
                [(0, "output", joined), (1, "input", low), (2, "input", high)],
            )
        ],
        [("fp:hi", "jn:2"), ("fp:lo", "jn:1"), ("jn:0", "fp:r")],
    ).function


def test_split_a_scalar_into_two_halves() -> None:
    result = split(U16, U8, U8)(0x1234)
    assert (result.hi, result.lo) == (0x12, 0x34)
    result = split(U32, U16, U16)(0xABCD1234)
    assert (result.hi, result.lo) == (0xABCD, 0x1234)


def test_split_a_signed_integer_gives_unsigned_halves() -> None:
    result = split(I16, U8, U8)(-2)
    assert (result.hi, result.lo) == (0xFF, 0xFE)


def test_split_an_8_bit_integer_returns_it_in_lo_and_zero_in_hi() -> None:
    result = split(U8, U8, U8)(0x7F)
    assert (result.hi, result.lo) == (0, 0x7F)


def test_split_an_array_of_integers_gives_two_arrays() -> None:
    result = split(array("NumUInt32"), array("NumUInt16"), array("NumUInt16"))(
        [0x00010002, 0xFFFF0000, 7]
    )
    assert result.hi == [1, 0xFFFF, 0]
    assert result.lo == [2, 0, 7]


def test_split_a_2d_array_keeps_its_shape() -> None:
    result = split(
        array("NumUInt16", 2), array("NumUInt8", 2), array("NumUInt8", 2)
    )([[0x0102, 0x0304], [0x0506]])
    assert result.hi == [[1, 3], [5]]
    assert result.lo == [[2, 4], [6]]


def test_split_a_cluster_of_integers_splits_each_field() -> None:
    record = cluster(("a", U16), ("b", U32))
    halves = cluster(("a", U8), ("b", U16))
    result = split(record, halves, halves)((0x0102, 0x00030004))
    assert result.hi == (1, 3)
    assert result.lo == (2, 4)


def test_split_an_array_of_clusters() -> None:
    record = cluster(("a", U16))
    halves = cluster(("a", U8))
    result = split(array(record), array(halves), array(halves))(
        [(0x0102,), (0x0304,)]
    )
    assert result.hi == [(1,), (3,)]
    assert result.lo == [(2,), (4,)]


def test_join_a_scalar_shifts_by_the_width_not_the_value() -> None:
    assert join(U8, U8, U16)(0x12, 0x34).r == 0x1234
    # lo of 1 has a bit length of 1, so a value-based shift would give 0x3
    assert join(U8, U8, U16)(0x01, 0x01).r == 0x0101
    assert join(U16, U16, U32)(0xABCD, 0x0001).r == 0xABCD0001


def test_join_of_different_widths_is_twice_the_widest() -> None:
    assert join(U8, U16, U32)(0x12, 0x3456).r == (0x12 << 16) | 0x3456


def test_join_arrays_element_by_element() -> None:
    result = join(array("NumUInt8"), array("NumUInt8"), array("NumUInt16"))(
        [1, 2], [3, 4]
    )
    assert result.r == [0x0103, 0x0204]


def test_join_a_signed_half_is_masked_to_its_width() -> None:
    assert join(I16, I16, U32)(-1, -1).r == 0xFFFFFFFF


def test_split_then_join_is_the_identity() -> None:
    value = 0xDEADBEEF
    halves = split(U32, U16, U16)(value)
    assert join(U16, U16, U32)(halves.hi, halves.lo).r == value


def test_join_of_clusters_is_a_gap_that_raises_when_run() -> None:
    import pytest

    halves = cluster(("a", U8))
    joined = cluster(("a", U16))
    function = join(halves, halves, joined)
    with pytest.raises(NotImplementedError, match="Join Numbers of a cluster"):
        function((1,), (2,))
