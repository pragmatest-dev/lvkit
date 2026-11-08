"""Decimal String To Number (1184): the digits of a string, from an offset."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.primitive_resolver import get_resolver
from tests.prim_vi import Pin, array_of, describe_and_lvnet, generate, scalar


def _vi(number_type: str = "NumInt32", default_wired: bool = True):
    controls = {"text": scalar("String"), "start": scalar("NumInt32")}
    if default_wired:
        controls["fallback"] = scalar(number_type)
    return generate(
        1184,
        "Decimal String To Number",
        [
            Pin(4, "input", scalar("String"), source="text"),
            Pin(3, "input", scalar("NumInt32"), source="start"),
            Pin(
                2,
                "input",
                scalar(number_type),
                source="fallback" if default_wired else None,
            ),
            Pin(0, "output", scalar(number_type), sink="number"),
            Pin(1, "output", scalar("NumInt32"), sink="past"),
        ],
        controls,
        {"number": scalar(number_type), "past": scalar("NumInt32")},
    )


def test_resolver_terminals() -> None:
    entry = get_resolver().resolve(prim_id=1184)
    assert entry is not None
    assert [(t.index, t.direction, t.name, t.type) for t in entry.terminals] == [
        (0, "output", "number", "polymorphic"),
        (1, "output", "offset_past_number", "NumInt32"),
        (2, "input", "default", "polymorphic"),
        (3, "input", "offset", "NumInt32"),
        (4, "input", "string", "String"),
    ]
    assert not entry.confidence == "placeholder"


def test_describe_and_lvnet_name_the_function(tmp_path: Path) -> None:
    text, lvnet = describe_and_lvnet(
        tmp_path, 1184, [(0, True), (1, True), (2, False), (3, False), (4, False)]
    )
    assert text.endswith("## Operations\nDecimal String To Number [prim 1184]")
    assert "function Decimal_String_To_Number_10 : Decimal String To Number" in lvnet


def test_ni_table_rows() -> None:
    """NI's three rows: string, offset, default -> offset past number, number."""
    source, fn = _vi()
    assert "lvkit" not in source
    row = fn("13ax", 0, 0)
    assert (row.number, row.past) == (13, 2)
    row = fn("-4.8bcde conversion", 0, 0)
    assert (row.number, row.past) == (-4, 2)
    row = fn("a49b", 0, -9)
    assert (row.number, row.past) == (-9, 0)


def test_offset_starts_the_conversion_there() -> None:
    _, fn = _vi()
    row = fn("ab123cd", 2, 0)
    assert (row.number, row.past) == (123, 5)


def test_negative_offset_is_zero() -> None:
    _, fn = _vi()
    row = fn("42x", -5, 0)
    assert (row.number, row.past) == (42, 2)


def test_offset_past_the_end_reads_nothing() -> None:
    _, fn = _vi()
    row = fn("42", 9, 7)
    assert (row.number, row.past) == (7, 9)


def test_sign_without_digits_reads_nothing() -> None:
    _, fn = _vi()
    row = fn("-x", 0, 3)
    assert (row.number, row.past) == (3, 0)


def test_unwired_default_is_zero_and_unwired_offset_is_zero() -> None:
    _, fn = _vi(default_wired=False)
    row = fn("no digits", 0)
    assert (row.number, row.past) == (0, 0)


def test_overflow_is_the_limit_of_the_number_type() -> None:
    _, i8 = _vi("NumInt8")
    assert i8("300", 0, 0).number == 127
    assert i8("-300", 0, 0).number == -128
    _, u8 = _vi("NumUInt8")
    assert u8("300", 0, 0).number == 255
    assert u8("-5", 0, 0).number == 0
    _, i32 = _vi()
    assert i32("99999999999", 0, 0).number == 2**31 - 1


def test_floating_point_default_still_stops_at_the_decimal_point() -> None:
    _, fn = _vi("NumFloat64")
    row = fn("-4.8", 0, 0.5)
    assert (row.number, row.past) == (-4.0, 2)
    assert isinstance(row.number, float)
    assert fn("x", 0, 0.5).number == 0.5


def test_array_of_strings_is_a_gap() -> None:
    _, fn = generate(
        1184,
        "Decimal String To Number",
        [
            Pin(4, "input", array_of(scalar("String")), source="text"),
            Pin(0, "output", array_of(scalar("NumInt32")), sink="number"),
        ],
        {"text": array_of(scalar("String"))},
        {"number": array_of(scalar("NumInt32"))},
    )
    with pytest.raises(NotImplementedError, match="converts a string"):
        fn([])
