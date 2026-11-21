"""Power Of 2 (1223): 2 raised to x, of the same numeric representation as x."""

from __future__ import annotations

import math

from tests.prim_vi import Pin, array_of, generate, scalar


def _vi(element: str = "NumFloat64", array: bool = False):
    kind = array_of(scalar(element)) if array else scalar(element)
    return generate(
        1223,
        "Power Of 2",
        [Pin(1, "input", kind, source="x"), Pin(0, "output", kind, sink="power")],
        {"x": kind},
        {"power": kind},
    )


def test_float_scalar_is_a_float() -> None:
    source, fn = _vi()
    assert "lvkit" not in source
    assert fn(3.0).power == 8.0
    assert fn(-1.0).power == 0.5
    assert fn(2000.0).power == math.inf


def test_integer_scalar_is_an_int_usable_as_a_range_bound() -> None:
    _, fn = _vi("NumInt32")
    result = fn(8).power
    assert result == 256 and type(result) is int
    assert len(range(result - 10)) == 246


def test_integer_wraps_to_the_width_of_the_type() -> None:
    _, fn = _vi("NumInt32")
    assert fn(30).power == 1 << 30
    assert fn(31).power == -(1 << 31)
    assert fn(32).power == 0
    assert fn(-3).power == 0
    _, fn8 = _vi("NumUInt8")
    assert fn8(7).power == 128
    assert fn8(8).power == 0


def test_integer_array_is_computed_element_by_element() -> None:
    _, fn = _vi("NumInt32", array=True)
    assert fn([0, 1, 10]).power == [1, 2, 1024]
