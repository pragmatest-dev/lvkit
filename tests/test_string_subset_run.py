"""String Subset runs as NI describes it: an offset below 0 is 0, a length of 0
or less gives the empty string, an unwired length is the rest of the string (NI,
"String Subset")."""

from __future__ import annotations

import pytest

from tests.native_vi import Generated, generate, node, result_values, scalar

STR = scalar("String")
I32 = scalar("NumInt32")


def _subset(with_length: bool) -> Generated:
    terminals = [
        (0, "output", STR),
        (1, "input", I32),
        (2, "input", I32),
        (3, "input", STR),
    ]
    wires = [("fp:s", "ss:3"), ("fp:o", "ss:2"), ("ss:0", "fp:r")]
    inputs = {"s": STR, "o": I32}
    if with_length:
        wires.append(("fp:n", "ss:1"))
        inputs["n"] = I32
    return generate(inputs, {"r": STR}, [node("ss", 1503, terminals)], wires)


@pytest.mark.parametrize(
    ("offset", "length", "expected"),
    [
        (1, 2, "bc"),
        (-1, 1, "a"),
        (-5, 2, "ab"),
        (1, -1, ""),
        (1, 0, ""),
        (2, 10, "cd"),
        (9, 2, ""),
    ],
)
def test_offset_and_length_are_clamped_at_zero(
    offset: int, length: int, expected: str
) -> None:
    function = _subset(True).function
    assert result_values(function("abcd", offset, length))[0] == expected


def test_an_unwired_length_is_the_rest_of_the_string() -> None:
    function = _subset(False).function
    assert result_values(function("abcd", 1))[0] == "bcd"
    assert result_values(function("abcd", -3))[0] == "abcd"
