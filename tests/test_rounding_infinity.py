"""Round Toward -Infinity / +Infinity and the To <integer> conversions, run on the
generated code: a floating-point input gives a floating-point whole number
(infinity stays infinity) and converts to an integer saturating at the range."""

from __future__ import annotations

import math

import pytest

from tests.prim_vi import Pin, array_of, generate, scalar

INF = math.inf


def _unary(prim_id: int, name: str, in_type, out_type):
    return generate(
        prim_id,
        name,
        [
            Pin(1, "input", in_type, source="n"),
            Pin(0, "output", out_type, sink="r"),
        ],
        {"n": in_type},
        {"r": out_type},
    )


@pytest.mark.parametrize("element", ["NumFloat64", "NumFloat32"])
def test_floor_and_ceil_of_floats_are_floats(element: str) -> None:
    kind = scalar(element)
    _, floor = _unary(1075, "Round Toward -Infinity", kind, kind)
    _, ceil = _unary(1076, "Round Toward +Infinity", kind, kind)
    assert [floor(x).r for x in (2.5, -2.5, INF, -INF)] == [2.0, -3.0, INF, -INF]
    assert [ceil(x).r for x in (2.5, -2.5, INF, -INF)] == [3.0, -2.0, INF, -INF]
    assert isinstance(floor(2.5).r, float) and isinstance(ceil(2.5).r, float)


def test_floor_and_ceil_of_integers_are_unchanged() -> None:
    kind = scalar("NumInt32")
    _, floor = _unary(1075, "Round Toward -Infinity", kind, kind)
    _, ceil = _unary(1076, "Round Toward +Infinity", kind, kind)
    assert floor(-7).r == -7 and ceil(9).r == 9
    assert isinstance(floor(3).r, int)


def test_floor_of_an_array() -> None:
    kind = array_of(scalar("NumFloat64"))
    _, floor = _unary(1075, "Round Toward -Infinity", kind, kind)
    assert floor([1.5, -1.5, -INF]).r == [1.0, -2.0, -INF]


def test_round_to_nearest_of_infinity_is_unchanged() -> None:
    kind = scalar("NumFloat64")
    _, nearest = _unary(1055, "Round To Nearest", kind, kind)
    assert [nearest(x).r for x in (2.5, -2.5, INF, -INF)] == [2.0, -2.0, INF, -INF]


_INTEGERS = [
    (1140, "To Byte Integer", "NumInt8", -128, 127),
    (1141, "To Word Integer", "NumInt16", -(2**15), 2**15 - 1),
    (1142, "To Long Integer", "NumInt32", -(2**31), 2**31 - 1),
    (1155, "To Quad Integer", "NumInt64", -(2**63), 2**63 - 1),
    (1143, "To Unsigned Byte Integer", "NumUInt8", 0, 255),
    (1144, "To Unsigned Word Integer", "NumUInt16", 0, 2**16 - 1),
    (1145, "To Unsigned Long Integer", "NumUInt32", 0, 2**32 - 1),
    (1156, "To Unsigned Quad Integer", "NumUInt64", 0, 2**64 - 1),
]


@pytest.mark.parametrize("element", ["NumFloat64", "NumFloat32"])
@pytest.mark.parametrize("prim_id,name,out,low,high", _INTEGERS)
def test_a_float_saturates_at_the_range(
    element: str, prim_id: int, name: str, out: str, low: int, high: int
) -> None:
    _, convert = _unary(prim_id, name, scalar(element), scalar(out))
    assert convert(INF).r == high
    assert convert(-INF).r == low
    assert convert(1e30).r == high
    assert convert(-1e30).r == low
    assert convert(2.5).r == 2
    assert convert(3.5).r == 4
    assert convert(-2.5).r == max(low, -2)
    assert isinstance(convert(2.5).r, int)


@pytest.mark.parametrize("prim_id,name,out,low,high", _INTEGERS)
def test_an_integer_keeps_its_low_bits(
    prim_id: int, name: str, out: str, low: int, high: int
) -> None:
    _, convert = _unary(prim_id, name, scalar("NumInt64"), scalar(out))
    assert convert(5).r == 5
    if high < 2**63 - 1:
        assert convert(high + 1).r == (low if low < 0 else 0)
    elif low == 0:
        assert convert(-1).r == high


def test_a_float_array_converts_each_element() -> None:
    _, convert = _unary(
        1142,
        "To Long Integer",
        array_of(scalar("NumFloat64")),
        array_of(scalar("NumInt32")),
    )
    assert convert([INF, -INF, 0.5, 1.5]).r == [2**31 - 1, -(2**31), 0, 2]


def test_nan_still_raises() -> None:
    _, convert = _unary(
        1142, "To Long Integer", scalar("NumFloat64"), scalar("NumInt32")
    )
    with pytest.raises(ValueError):
        convert(math.nan)
