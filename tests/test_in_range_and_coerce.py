"""In Range and Coerce (primitive 1127) runs as Python on a scalar and, element
by element, on an array.

NI (In Range and Coerce): "you can wire an array to x and two scalars to upper
limit and lower limit"; "In Compare Elements mode, the data type structure of
In Range? matches the data type structure of x, with each scalar replaced by a
Boolean value"; coerced(x) is x, or the lower or upper limit when x is outside;
"If the lower limit value is greater than the upper limit value, In Range? is
always FALSE and LabVIEW switches the lower limit value and the upper limit
value internally before computing coerced(x)"."""

from __future__ import annotations

from tests.native_vi import array
from tests.primitive_caller import run_primitive
from tests.vilib_caller import BOOL, DBL

BOOL_ARRAY = array("Boolean")
DBL_ARRAY = array("NumFloat64")


def _run(lower, x, upper, *, array_limits: bool = False):
    x_is_array = isinstance(x, list)
    limit_type = DBL_ARRAY if array_limits else DBL
    pane = [
        (0, "output", BOOL_ARRAY if x_is_array else BOOL),
        (1, "output", DBL_ARRAY if x_is_array else DBL),
        (2, "input", limit_type),
        (3, "input", DBL_ARRAY if x_is_array else DBL),
        (4, "input", limit_type),
    ]
    inputs = {
        "lower": (2, limit_type),
        "x": (3, DBL_ARRAY if x_is_array else DBL),
        "upper": (4, limit_type),
    }
    outputs = {
        "in_range": (0, BOOL_ARRAY if x_is_array else BOOL),
        "coerced": (1, DBL_ARRAY if x_is_array else DBL),
    }
    return run_primitive(
        1127,
        pane,
        inputs,
        outputs,
        {"lower": lower, "x": x, "upper": upper},
        name="InRange",
    )[1]


def test_scalar_in_range_is_unchanged() -> None:
    result = _run(48.0, 50.0, 57.0)
    assert result.in_range is True
    assert result.coerced == 50.0


def test_scalar_limits_are_included_and_outside_values_are_coerced() -> None:
    assert _run(48.0, 57.0, 57.0).in_range is True
    assert _run(48.0, 47.0, 57.0).in_range is False
    assert _run(48.0, 47.0, 57.0).coerced == 48.0
    assert _run(48.0, 60.0, 57.0).coerced == 57.0


def test_swapped_limits_are_never_in_range_and_coerce_between_them() -> None:
    result = _run(57.0, 60.0, 48.0)
    assert result.in_range is False
    assert result.coerced == 57.0


def test_array_x_with_scalar_limits_is_element_wise() -> None:
    result = _run(48.0, [47.0, 48.0, 52.0, 57.0, 58.0], 57.0)
    assert result.in_range == [False, True, True, True, False]
    assert result.coerced == [48.0, 48.0, 52.0, 57.0, 57.0]


def test_array_x_with_array_limits_pairs_the_elements() -> None:
    result = _run(
        [0.0, 10.0, 20.0],
        [-5.0, 15.0, 30.0],
        [5.0, 12.0, 25.0],
        array_limits=True,
    )
    assert result.in_range == [False, False, False]
    assert result.coerced == [0.0, 12.0, 25.0]


def test_the_generated_array_code_is_a_comprehension() -> None:
    pane = [
        (0, "output", BOOL_ARRAY),
        (1, "output", DBL_ARRAY),
        (2, "input", DBL),
        (3, "input", DBL_ARRAY),
        (4, "input", DBL),
    ]
    source, _ = run_primitive(
        1127,
        pane,
        {"lower": (2, DBL), "x": (3, DBL_ARRAY), "upper": (4, DBL)},
        {"in_range": (0, BOOL_ARRAY)},
        {"lower": 48.0, "x": [1.0], "upper": 57.0},
        name="InRange",
    )
    assert "for _e0 in x" in source
