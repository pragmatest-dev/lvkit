"""Hexadecimal String To Number (1185) and Fract/Exp String To Number (1187)."""

from __future__ import annotations

import locale
import math

import pytest

from tests.prim_vi import Pin, array_of, generate, scalar


def _hex(number_type: str = "NumUInt32", default_wired: bool = True):
    controls = {"text": scalar("String"), "start": scalar("NumInt32")}
    if default_wired:
        controls["fallback"] = scalar(number_type)
    return generate(
        1185,
        "Hexadecimal String To Number",
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


def test_hex_ni_table_rows() -> None:
    source, fn = _hex()
    assert "lvkit" not in source
    row = fn("3ab", 0, 0)
    assert (row.number, row.past) == (939, 3)
    row = fn("3ab", 2, 0)
    assert (row.number, row.past) == (11, 3)
    row = fn("f3g", 0, 0)
    assert (row.number, row.past) == (243, 2)
    row = fn("-30", 0, 0)
    assert (row.number, row.past) == (0, 0)


def test_hex_saturates_at_the_type_of_default() -> None:
    _, u32 = _hex()
    assert u32("1FFFFFFFF", 0, 0).number == 4294967295
    _, u64 = _hex("NumUInt64")
    assert u64("1FFFFFFFF", 0, 0).number == 8589934591
    _, i8 = _hex("NumInt8")
    assert i8("FF", 0, 0).number == 127


def test_hex_no_digits_is_the_default_at_the_offset() -> None:
    _, fn = _hex()
    row = fn("xyz", 1, 77)
    assert (row.number, row.past) == (77, 1)


def test_hex_upper_and_lower_case_and_negative_offset() -> None:
    _, fn = _hex()
    assert fn("aBc", -4, 0).number == 0xABC


def test_hex_unwired_default_is_zero() -> None:
    _, fn = _hex(default_wired=False)
    assert fn("zz", 0).number == 0


def test_hex_array_of_strings_is_a_gap() -> None:
    _, fn = generate(
        1185,
        "Hexadecimal String To Number",
        [
            Pin(4, "input", array_of(scalar("String")), source="text"),
            Pin(0, "output", array_of(scalar("NumUInt32")), sink="number"),
        ],
        {"text": array_of(scalar("String"))},
        {"number": array_of(scalar("NumUInt32"))},
    )
    with pytest.raises(NotImplementedError):
        fn([])


def _fract(number_type: str = "NumFloat64", system_wired: bool = False):
    controls = {
        "text": scalar("String"),
        "start": scalar("NumInt32"),
        "fallback": scalar(number_type),
    }
    pins = [
        Pin(5, "input", scalar("String"), source="text"),
        Pin(4, "input", scalar("NumInt32"), source="start"),
        Pin(3, "input", scalar(number_type), source="fallback"),
        Pin(0, "output", scalar(number_type), sink="number"),
        Pin(1, "output", scalar("NumInt32"), sink="past"),
    ]
    if system_wired:
        controls["system"] = scalar("Boolean")
        pins.append(Pin(2, "input", scalar("Boolean"), source="system"))
    return generate(
        1187,
        "Fract/Exp String To Number",
        pins,
        controls,
        {"number": scalar(number_type), "past": scalar("NumInt32")},
    )


def test_fract_exp_ni_table_rows() -> None:
    source, fn = _fract()
    assert "lvkit" not in source
    row = fn("-4.7e-3x", 0, 0.0)
    assert (row.number, row.past) == (-0.0047, 7)
    row = fn("+5.3.2", 0, 0.0)
    assert (row.number, row.past) == (5.3, 4)


def test_fract_exp_offset_and_default() -> None:
    _, fn = _fract()
    row = fn("ab 12.5e2 z", 3, 9.0)
    assert (row.number, row.past) == (1250.0, 9)
    row = fn("nothing", 0, 9.5)
    assert (row.number, row.past) == (9.5, 0)
    assert fn("1.5", -3, 0.0).number == 1.5


def test_fract_exp_exponent_without_digits_is_not_part_of_the_number() -> None:
    _, fn = _fract()
    row = fn("5e+x", 0, 0.0)
    assert (row.number, row.past) == (5.0, 1)
    assert fn(".5", 0, 0.0).number == 0.5
    assert fn("5.", 0, 0.0).number == 5.0
    assert fn("-.", 0, 7.0).number == 7.0


def test_fract_exp_inf_and_nan() -> None:
    _, fn = _fract()
    assert fn("Inf", 0, 0.0).number == math.inf
    row = fn("-Inf", 0, 0.0)
    assert (row.number, row.past) == (-math.inf, 4)
    assert math.isnan(fn("NaN", 0, 0.0).number)


def test_fract_exp_too_large_for_a_double_is_the_largest_double() -> None:
    _, fn = _fract()
    assert fn("1e999", 0, 0.0).number == 1.7976931348623157e308
    assert fn("-1e999", 0, 0.0).number == -1.7976931348623157e308


def test_fract_exp_into_an_integer_rounds_to_even_and_limits() -> None:
    _, u8 = _fract("NumUInt8")
    assert u8("2.5", 0, 0).number == 2
    assert u8("3.5", 0, 0).number == 4
    assert u8("300", 0, 0).number == 255
    assert u8("-5", 0, 0).number == 0
    assert u8("Inf", 0, 0).number == 255
    _, i32 = _fract("NumInt32")
    assert i32("-1e99", 0, 0).number == -(2**31)
    assert isinstance(i32("7", 0, 0).number, int)


def test_fract_exp_system_decimal_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(locale, "localeconv", lambda: {"decimal_point": ","})
    _, fn = _fract(system_wired=True)
    assert fn("1,5", 0, 0.0, True).number == 1.5
    row = fn("1,5", 0, 0.0, False)
    assert (row.number, row.past) == (1.0, 1)
    assert fn("1.5", 0, 0.0, False).number == 1.5


def test_fract_exp_unwired_system_decimal_point_is_the_localized_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(locale, "localeconv", lambda: {"decimal_point": ","})
    _, fn = _fract()
    assert fn("1,5", 0, 0.0).number == 1.5
