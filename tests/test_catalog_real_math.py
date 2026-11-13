"""Square Root, Square, Inverse Cosine and Polar To Re/Im run as Python."""

from __future__ import annotations

import math

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import LVType, LVTypeKind
from tests.primitive_caller import run_primitive
from tests.vilib_caller import DBL, I32, primitive

U8 = primitive("NumUInt8")
DBL_ARRAY = LVType(kind=LVTypeKind.ARRAY, element_type=DBL, dimensions=1)


def _unary(prim_id: int, x_type: LVType, out_type: LVType, value):
    return run_primitive(
        prim_id,
        [(0, "output", out_type), (1, "input", x_type)],
        {"x": (1, x_type)},
        {"result": (0, out_type)},
        {"x": value},
    )


def test_square_root() -> None:
    source, result = _unary(1060, DBL, DBL, 9.0)
    assert result.result == 3.0
    assert "lvkit" not in source


def test_square_root_of_a_negative_number_is_nan() -> None:
    _, result = _unary(1060, DBL, DBL, -4.0)
    assert math.isnan(result.result)


def test_square_root_of_an_integer_is_a_double() -> None:
    _, result = _unary(1060, I32, DBL, 16)
    assert result.result == 4.0


def test_square_root_of_an_array_is_per_element() -> None:
    _, result = _unary(1060, DBL_ARRAY, DBL_ARRAY, [4.0, -1.0, 0.0])
    assert result.result[0] == 2.0
    assert math.isnan(result.result[1])
    assert result.result[2] == 0.0


def test_square() -> None:
    _, result = _unary(23059, DBL, DBL, -1.5)
    assert result.result == 2.25


def test_square_of_an_array() -> None:
    _, result = _unary(23059, DBL_ARRAY, DBL_ARRAY, [1.0, -2.0, 3.0])
    assert result.result == [1.0, 4.0, 9.0]


def test_square_of_an_integer_wraps_at_its_width() -> None:
    _, result = _unary(23059, I32, I32, 65536)
    assert result.result == 0
    _, result = _unary(23059, I32, I32, 46341)
    assert result.result == 46341 * 46341 - (1 << 32)
    _, result = _unary(23059, U8, U8, 20)
    assert result.result == 400 % 256


def test_inverse_cosine() -> None:
    _, result = _unary(1207, DBL, DBL, 0.5)
    assert result.result == math.acos(0.5)


def test_inverse_cosine_out_of_range_is_nan() -> None:
    _, result = _unary(1207, DBL, DBL, 1.5)
    assert math.isnan(result.result)
    _, result = _unary(1207, DBL, DBL, -1.0)
    assert result.result == math.pi


def _polar(r_type: LVType, theta_type: LVType, out_type: LVType, r, theta):
    return run_primitive(
        23061,
        [
            (0, "output", out_type),
            (1, "output", out_type),
            (2, "input", theta_type),
            (3, "input", r_type),
        ],
        {"r": (3, r_type), "theta": (2, theta_type)},
        {"x": (1, out_type), "y": (0, out_type)},
        {"r": r, "theta": theta},
    )


def test_polar_to_re_im() -> None:
    _, result = _polar(DBL, DBL, DBL, 2.0, math.pi / 2)
    assert math.isclose(result.x, 0.0, abs_tol=1e-12)
    assert math.isclose(result.y, 2.0)


def test_polar_to_re_im_of_arrays() -> None:
    _, result = _polar(DBL_ARRAY, DBL, DBL_ARRAY, [1.0, 2.0], 0.0)
    assert result.x == [1.0, 2.0]
    assert result.y == [0.0, 0.0]


def test_polar_to_re_im_infinite_angle_is_nan() -> None:
    _, result = _polar(DBL, DBL, DBL, 1.0, math.inf)
    assert math.isnan(result.x) and math.isnan(result.y)


def test_a_neighbour_without_python_stays_a_gap() -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        run_primitive(
            1203,
            [(0, "output", DBL), (1, "input", DBL)],
            {"x": (1, DBL)},
            {"result": (0, DBL)},
            {"x": 1.0},
            gaps=gaps,
        )
    assert gaps
