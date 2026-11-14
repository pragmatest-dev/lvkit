"""Replace Substring (3913) and To Quad Integer (1155)."""

from __future__ import annotations

import pytest

from tests.prim_vi import Pin, array_of, generate, scalar


def _replace(length: bool, offset: bool, substring: bool = True):
    controls = {"text": scalar("String")}
    pins = [
        Pin(5, "input", scalar("String"), source="text"),
        Pin(4, "input", scalar("String"), source="sub" if substring else None),
        Pin(3, "input", scalar("NumInt32"), source="at" if offset else None),
        Pin(2, "input", scalar("NumInt32"), source="count" if length else None),
        Pin(1, "output", scalar("String"), sink="result"),
        Pin(0, "output", scalar("String"), sink="replaced"),
    ]
    if substring:
        controls["sub"] = scalar("String")
    if offset:
        controls["at"] = scalar("NumInt32")
    if length:
        controls["count"] = scalar("NumInt32")
    return generate(
        3913,
        "Replace Substring",
        pins,
        controls,
        {"result": scalar("String"), "replaced": scalar("String")},
    )


def test_replace_length_bytes_at_offset() -> None:
    source, fn = _replace(True, True)
    assert "lvkit" not in source
    row = fn("hello world", "THERE", 6, 5)
    assert (row.result, row.replaced) == ("hello THERE", "world")


def test_length_zero_inserts() -> None:
    _, fn = _replace(True, True)
    row = fn("abcd", "XY", 2, 0)
    assert (row.result, row.replaced) == ("abXYcd", "")


def test_empty_substring_deletes() -> None:
    _, fn = _replace(True, True)
    row = fn("abcdef", "", 1, 3)
    assert (row.result, row.replaced) == ("aef", "bcd")


def test_unwired_length_is_the_length_of_the_substring() -> None:
    _, fn = _replace(False, True)
    row = fn("abcdef", "XY", 2)
    assert (row.result, row.replaced) == ("abXYef", "cd")


def test_unwired_offset_is_zero_and_unwired_substring_deletes() -> None:
    _, fn = _replace(True, False, substring=False)
    row = fn("abcdef", 2)
    assert (row.result, row.replaced) == ("cdef", "ab")


def test_offset_below_zero_is_zero_and_past_the_end_appends() -> None:
    _, fn = _replace(True, True)
    assert fn("abc", "X", -4, 0).result == "Xabc"
    assert fn("abc", "X", 9, 0).result == "abcX"


def _quad(in_type: str, out_type: str = "NumInt64"):
    return generate(
        1155,
        "To Quad Integer",
        [
            Pin(1, "input", scalar(in_type), source="n"),
            Pin(0, "output", scalar(out_type), sink="q"),
        ],
        {"n": scalar(in_type)},
        {"q": scalar(out_type)},
    )


def test_to_quad_integer_rounds_halves_to_even() -> None:
    _, fn = _quad("NumFloat64")
    assert [fn(x).q for x in (13.5, 14.5, -2.5, 2.4)] == [14, 14, -2, 2]


def test_to_quad_integer_of_an_integer_and_the_limits() -> None:
    _, fn = _quad("NumInt32")
    assert fn(-7).q == -7
    _, fn = _quad("NumFloat64")
    assert fn(1e30).q == 2**63 - 1
    assert fn(-1e30).q == -(2**63)


def test_to_quad_integer_of_an_array() -> None:
    _, fn = generate(
        1155,
        "To Quad Integer",
        [
            Pin(1, "input", array_of(scalar("NumFloat64")), source="n"),
            Pin(0, "output", array_of(scalar("NumInt64")), sink="q"),
        ],
        {"n": array_of(scalar("NumFloat64"))},
        {"q": array_of(scalar("NumInt64"))},
    )
    assert fn([0.5, 1.5, 2.5]).q == [0, 2, 2]


def test_a_neighbour_without_python_stays_a_gap() -> None:
    _, fn = generate(
        2019,
        "Unknown",
        [
            Pin(1, "input", scalar("NumFloat64"), source="n"),
            Pin(0, "output", scalar("NumUInt64"), sink="q"),
        ],
        {"n": scalar("NumFloat64")},
        {"q": scalar("NumUInt64")},
    )
    with pytest.raises(NotImplementedError):
        fn(1.0)
