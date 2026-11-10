"""Round To Nearest (1055): the nearest integer, a tie to the even one."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from lvkit.primitive_resolver import get_resolver
from tests.prim_vi import Pin, array_of, describe_and_lvnet, generate, scalar


def _vi(element: str = "NumFloat64", array: bool = False):
    kind = array_of(scalar(element)) if array else scalar(element)
    return generate(
        1055,
        "Round To Nearest",
        [
            Pin(1, "input", kind, source="number"),
            Pin(0, "output", kind, sink="nearest"),
        ],
        {"number": kind},
        {"nearest": kind},
    )


def test_resolver_terminals() -> None:
    entry = get_resolver().resolve(prim_id=1055)
    assert entry is not None
    assert [(t.index, t.direction, t.name, t.type) for t in entry.terminals] == [
        (0, "output", "nearest integer value", "NumFloat64"),
        (1, "input", "number", "NumFloat64"),
    ]


def test_describe_and_lvnet_name_the_function(tmp_path: Path) -> None:
    text, lvnet = describe_and_lvnet(tmp_path, 1055, [(0, True), (1, False)])
    assert text.endswith("## Operations\nRound To Nearest [prim 1055]")
    assert "function Round_To_Nearest_10 : Round To Nearest" in lvnet


def test_a_tie_goes_to_the_nearest_even_integer() -> None:
    """NI: "if number is 1.5 or 2.5, nearest integer value is 2"."""
    source, fn = _vi()
    assert "lvkit" not in source
    assert [fn(x).nearest for x in (1.5, 2.5, 3.5, 4.5, -0.5, -1.5, -2.5)] == [
        2.0,
        2.0,
        4.0,
        4.0,
        0.0,
        -2.0,
        -2.0,
    ]


def test_other_values_go_to_the_nearest_integer() -> None:
    _, fn = _vi()
    assert [fn(x).nearest for x in (2.4, 2.6, -2.4, -2.6, 0.0, 7.0)] == [
        2.0,
        3.0,
        -2.0,
        -3.0,
        0.0,
        7.0,
    ]


def test_the_result_is_floating_point_like_the_input() -> None:
    _, fn = _vi()
    assert isinstance(fn(2.5).nearest, float)


def test_infinity_and_nan_are_returned_as_they_are() -> None:
    _, fn = _vi()
    assert fn(math.inf).nearest == math.inf
    assert fn(-math.inf).nearest == -math.inf
    assert math.isnan(fn(math.nan).nearest)


def test_array_is_rounded_element_by_element() -> None:
    _, fn = _vi(array=True)
    assert fn([0.5, 1.5, 2.5, 3.5, -2.5]).nearest == [0.0, 2.0, 2.0, 4.0, -2.0]


def test_single_precision() -> None:
    _, fn = _vi("NumFloat32")
    assert fn(2.5).nearest == 2.0


def test_integer_is_its_own_nearest_integer() -> None:
    _, fn = _vi("NumInt32")
    assert fn(7).nearest == 7


def test_complex_is_a_gap() -> None:
    _, fn = _vi("NumComplex128")
    with pytest.raises(NotImplementedError, match="real numbers"):
        fn(1 + 2j)
