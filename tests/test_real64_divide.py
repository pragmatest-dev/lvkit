"""DBL Divide code generation preserves IEEE results at signed zero."""

from __future__ import annotations

import math
from functools import cache

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from tests.helpers import build_graph


def numeric_type(underlying: str | None, rank: int = 0) -> LVType | None:
    if underlying is None:
        return None
    scalar = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=underlying)
    if rank:
        return LVType(kind=LVTypeKind.ARRAY, element_type=scalar, dimensions=rank)
    return scalar


@cache
def generated_divide(underlying="NumFloat64", numerator_rank=0, denominator_rank=0):
    types = [
        numeric_type(underlying, numerator_rank),
        numeric_type(underlying, denominator_rank),
        numeric_type(underlying, max(numerator_rank, denominator_rank)),
    ]
    inputs = [
        FPTerminal(
            id=f"fp:{name}",
            index=index,
            direction="input",
            name=name,
            is_public=True,
            is_indicator=False,
            lv_type=types[index],
        )
        for index, name in enumerate(("numerator", "denominator"))
    ]
    result = FPTerminal(
        id="fp:quotient",
        index=0,
        direction="output",
        name="quotient",
        is_public=True,
        is_indicator=True,
        lv_type=types[2],
    )
    primitive = PrimitiveNode(
        id="divide",
        vi_path="Divide Test.vi",
        name="Divide",
        prim_id=1053,
        terminals=[
            Terminal(id="divide:x", index=2, direction="input", lv_type=types[0]),
            Terminal(id="divide:y", index=1, direction="input", lv_type=types[1]),
            Terminal(id="divide:q", index=0, direction="output", lv_type=types[2]),
        ],
    )
    pairs = [
        (inputs[0].id, "divide:x"),
        (inputs[1].id, "divide:y"),
        ("divide:q", result.id),
    ]
    context = VIContext(
        name="Divide Test.vi",
        inputs=inputs,
        outputs=[result],
        data_flow=[
            Wire.from_terminals(from_terminal_id=source, to_terminal_id=target)
            for source, target in pairs
        ],
    )
    graph = build_graph(context, context.name, [primitive])
    source = build_module(context, context.name, graph=graph)
    namespace = {}
    exec(compile(source, context.name, "exec"), namespace)
    return namespace["divide_test"]


def assert_numeric(actual, expected):
    if isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected)
        for value, target in zip(actual, expected):
            assert_numeric(value, target)
    elif math.isnan(expected):
        assert math.isnan(actual)
    else:
        assert actual == expected
        assert math.copysign(1.0, actual) == math.copysign(1.0, expected)


@pytest.mark.parametrize(
    "numerator,denominator,expected",
    [
        (1.0, 0.0, math.inf),
        (1.0, -0.0, -math.inf),
        (-1.0, 0.0, -math.inf),
        (-1.0, -0.0, math.inf),
        (0.0, 0.0, math.nan),
        (0.0, -0.0, math.nan),
        (-0.0, 0.0, math.nan),
        (-0.0, -0.0, math.nan),
        (math.nan, 0.0, math.nan),
        (math.nan, -0.0, math.nan),
        (math.inf, 0.0, math.inf),
        (math.inf, -0.0, -math.inf),
        (-math.inf, 0.0, -math.inf),
        (-math.inf, -0.0, math.inf),
        (2.0, 4.0, 0.5),
        (-2.0, 4.0, -0.5),
        (0.0, -2.0, -0.0),
        (-0.0, -2.0, 0.0),
        (math.inf, math.inf, math.nan),
        (2.0, math.inf, 0.0),
        (2.0, -math.inf, -0.0),
        (math.nan, 2.0, math.nan),
        (2.0, math.nan, math.nan),
        (1e308, 1e-308, math.inf),
    ],
)
def test_generated_dbl_scalar(numerator, denominator, expected):
    assert_numeric(generated_divide()(numerator, denominator).quotient, expected)


@pytest.mark.parametrize(
    "numerator,denominator,numerator_rank,denominator_rank,expected",
    [
        ([1.0, 0.0, -1.0], -0.0, 1, 0, [-math.inf, math.nan, math.inf]),
        (1.0, [0.0, -0.0, 2.0], 0, 1, [math.inf, -math.inf, 0.5]),
        (
            [[1.0, -1.0], [0.0, 2.0]],
            0.0,
            2,
            0,
            [[math.inf, -math.inf], [math.nan, math.inf]],
        ),
        ([2.0, 6.0, 8.0], [4.0, 2.0], 1, 1, [0.5, 3.0]),
        ([], 0.0, 1, 0, []),
    ],
)
def test_generated_dbl_arrays(
    numerator, denominator, numerator_rank, denominator_rank, expected
):
    function = generated_divide("NumFloat64", numerator_rank, denominator_rank)
    assert_numeric(function(numerator, denominator).quotient, expected)


@pytest.mark.parametrize(
    "underlying", ["NumFloat32", "NumInt32", "NumComplex128", None]
)
def test_other_representations_keep_finite_operand_order(underlying):
    assert generated_divide(underlying)(2.0, 4.0).quotient == 0.5


@pytest.mark.parametrize("underlying", ["NumFloat32", "NumFloatExt"])
@pytest.mark.parametrize(
    "numerator,denominator,expected",
    [(1.0, 0.0, math.inf), (1.0, -0.0, -math.inf), (0.0, 0.0, math.nan)],
)
def test_other_float_representations_follow_ieee(
    underlying, numerator, denominator, expected
):
    quotient = generated_divide(underlying)(numerator, denominator).quotient
    assert_numeric(quotient, expected)


@pytest.mark.parametrize("underlying", ["NumInt32", "NumComplex128", None])
def test_other_representations_keep_existing_zero_behavior(underlying):
    with pytest.raises(ZeroDivisionError):
        generated_divide(underlying)(1.0, 0.0)
