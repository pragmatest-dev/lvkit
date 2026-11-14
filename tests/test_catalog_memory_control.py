"""Swap Values, Always Copy and Request Deallocation run as Python."""

from __future__ import annotations

import gc

import pytest

from lvkit.models import LVType, LVTypeKind
from tests.primitive_caller import run_primitive
from tests.vilib_caller import BOOL, DBL, STRING, U32

DBL_ARRAY = LVType(kind=LVTypeKind.ARRAY, element_type=DBL, dimensions=1)


def _swap(value_type: LVType, x, y, swap_wired: bool, swap: bool = True):
    pane = [
        (1, "output", value_type),
        (3, "output", value_type),
        (12, "input", value_type),
        (13, "input", BOOL),
        (14, "input", value_type),
    ]
    inputs = {"y": (12, value_type), "x": (14, value_type)}
    arguments = {"y": y, "x": x}
    if swap_wired:
        inputs["swap"] = (13, BOOL)
        arguments["swap"] = swap
    return run_primitive(
        2401,
        pane,
        inputs,
        {"y_out": (1, value_type), "x_out": (3, value_type)},
        arguments,
    )


@pytest.mark.parametrize(
    ("value_type", "x", "y"),
    [(U32, 1, 2), (STRING, "a", "b"), (DBL_ARRAY, [1.0], [2.0, 3.0])],
)
def test_swap_values_gives_each_output_the_other_input(value_type, x, y) -> None:
    source, result = _swap(value_type, x, y, swap_wired=False)
    assert (result.y_out, result.x_out) == (x, y)
    assert "lvkit" not in source


def test_swap_values_switched_off_passes_the_values_through() -> None:
    _, result = _swap(U32, 1, 2, swap_wired=True, swap=False)
    assert (result.y_out, result.x_out) == (2, 1)
    _, result = _swap(U32, 1, 2, swap_wired=True, swap=True)
    assert (result.y_out, result.x_out) == (1, 2)


def test_always_copy_is_a_new_array_with_the_same_values() -> None:
    source, result = run_primitive(
        2400,
        [(0, "output", DBL_ARRAY), (1, "input", DBL_ARRAY)],
        {"data": (1, DBL_ARRAY)},
        {"copied": (0, DBL_ARRAY)},
        {"data": (data := [1.0, 2.0])},
    )
    assert result.copied == data
    assert result.copied is not data
    assert "copy.copy" in source


def test_always_copy_of_a_string() -> None:
    _, result = run_primitive(
        2400,
        [(0, "output", STRING), (1, "input", STRING)],
        {"data": (1, STRING)},
        {"copied": (0, STRING)},
        {"data": "abc"},
    )
    assert result.copied == "abc"


@pytest.mark.parametrize("flag", [True, False])
def test_request_deallocation_collects_garbage_only_when_flagged(
    monkeypatch: pytest.MonkeyPatch, flag: bool
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(gc, "collect", lambda *a: calls.append(1) or 0)
    source, _ = run_primitive(
        1610,
        [(0, "input", BOOL)],
        {"flag": (0, BOOL)},
        {},
        {"flag": flag},
    )
    assert calls == ([1] if flag else [])
    assert "lvkit" not in source
