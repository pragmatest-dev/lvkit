"""Power Of 10 (1212): 10 raised to x, for floating-point scalars and arrays."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from lvkit.primitive_resolver import get_resolver
from tests.prim_vi import Pin, array_of, describe_and_lvnet, generate, scalar


def _vi(element: str = "NumFloat64", array: bool = False):
    kind = array_of(scalar(element)) if array else scalar(element)
    return generate(
        1212,
        "Power Of 10",
        [Pin(1, "input", kind, source="x"), Pin(0, "output", kind, sink="power")],
        {"x": kind},
        {"power": kind},
    )


def test_resolver_terminals() -> None:
    entry = get_resolver().resolve(prim_id=1212)
    assert entry is not None
    assert [(t.index, t.direction, t.name, t.type) for t in entry.terminals] == [
        (0, "output", "10^x", "NumFloat64"),
        (1, "input", "x", "NumFloat64"),
    ]


def test_describe_and_lvnet_name_the_function(tmp_path: Path) -> None:
    text, lvnet = describe_and_lvnet(tmp_path, 1212, [(0, True), (1, False)])
    assert text.endswith("## Operations\nPower Of 10 [prim 1212]")
    assert "function Power_Of_10_10 : Power Of 10" in lvnet


def test_scalar_values() -> None:
    source, fn = _vi()
    assert "lvkit" not in source
    assert fn(0.0).power == 1.0
    assert fn(2.0).power == 100.0
    assert fn(-1.0).power == pytest.approx(0.1)
    assert fn(0.5).power == pytest.approx(math.sqrt(10))


def test_a_result_past_the_largest_double_is_infinity() -> None:
    _, fn = _vi()
    assert fn(308.0).power == pytest.approx(1e308)
    assert fn(309.0).power == math.inf
    assert fn(1e6).power == math.inf
    assert fn(-1e6).power == 0.0
    assert fn(math.inf).power == math.inf
    assert fn(-math.inf).power == 0.0
    assert math.isnan(fn(math.nan).power)


def test_array_is_computed_element_by_element() -> None:
    _, fn = _vi(array=True)
    assert fn([0.0, 1.0, 400.0]).power == [1.0, 10.0, math.inf]


def test_single_precision_scalar() -> None:
    _, fn = _vi("NumFloat32")
    assert fn(3.0).power == 1000.0


def test_integer_is_a_gap() -> None:
    _, fn = _vi("NumInt32")
    with pytest.raises(NotImplementedError, match="floating-point"):
        fn(2)
