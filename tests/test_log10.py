"""Generated DBL Logarithm Base 10 preserves scalar and array semantics."""

from __future__ import annotations

import math
from functools import cache

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.nodes.base import CodeGenError
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.runtime import lv
from tests.helpers import build_graph


def numeric_type(underlying: str | None, rank: int = 0) -> LVType | None:
    if underlying is None:
        return None
    scalar = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=underlying)
    if rank:
        return LVType(kind=LVTypeKind.ARRAY, element_type=scalar, dimensions=rank)
    return scalar


@cache
def generated_log10(rank=0, input_type="NumFloat64", output_type="NumFloat64"):
    types = [numeric_type(input_type, rank), numeric_type(output_type, rank)]
    argument = FPTerminal(
        id="fp:values",
        index=0,
        direction="input",
        name="values",
        is_public=True,
        is_indicator=False,
        lv_type=types[0],
    )
    result = FPTerminal(
        id="fp:log_values",
        index=0,
        direction="output",
        name="log_values",
        is_public=True,
        is_indicator=True,
        lv_type=types[1],
    )
    primitive = PrimitiveNode(
        id="log10",
        vi_path="Log10 Test.vi",
        name="Logarithm Base 10",
        prim_id=1210,
        terminals=[
            Terminal(id="log10:in", index=1, direction="input", lv_type=types[0]),
            Terminal(id="log10:out", index=0, direction="output", lv_type=types[1]),
        ],
    )
    context = VIContext(
        name="Log10 Test.vi",
        inputs=[argument],
        outputs=[result],
        data_flow=[
            Wire.from_terminals(
                from_terminal_id=argument.id, to_terminal_id="log10:in"
            ),
            Wire.from_terminals(from_terminal_id="log10:out", to_terminal_id=result.id),
        ],
    )
    graph = build_graph(context, context.name, [primitive])
    source = build_module(context, context.name, graph=graph)
    namespace = {}
    exec(compile(source, context.name, "exec"), namespace)
    return namespace["log10_test"]


def assert_numeric(actual, expected):
    if isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected)
        for value, target in zip(actual, expected):
            assert_numeric(value, target)
    elif math.isnan(expected):
        assert math.isnan(actual)
    elif math.isinf(expected) or expected == 0.0:
        assert actual == expected
        assert math.copysign(1.0, actual) == math.copysign(1.0, expected)
    else:
        assert math.isclose(actual, expected, rel_tol=1e-14, abs_tol=1e-14)


def fingerprint(value):
    if isinstance(value, list):
        return [fingerprint(item) for item in value]
    return value.hex() if isinstance(value, float) else value


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.01, -2.0),
        (1.0, 0.0),
        (10.0, 1.0),
        (2.0, 0.3010299956639812),
        (0.0, -math.inf),
        (-0.0, -math.inf),
        (-1.0, math.nan),
        (math.inf, math.inf),
        (-math.inf, math.nan),
        (math.nan, math.nan),
        (1e300, 300.0),
        (1e-300, -300.0),
        (5e-324, -323.3062153431158),
    ],
)
def test_generated_dbl_scalar(value, expected):
    assert_numeric(generated_log10()(value).log_values, expected)


@pytest.mark.parametrize(
    "values,rank,expected",
    [
        ([], 1, []),
        ([1.0], 1, [0.0]),
        ([0.01, 1.0, 100.0], 1, [-2.0, 0.0, 2.0]),
        (
            [0.0, -0.0, -1.0, math.nan, math.inf, -math.inf],
            1,
            [-math.inf, -math.inf, math.nan, math.nan, math.inf, math.nan],
        ),
        ([[1.0, 10.0], [0.0, -1.0]], 2, [[0.0, 1.0], [-math.inf, math.nan]]),
        ([[], []], 2, [[], []]),
    ],
)
def test_generated_dbl_arrays(values, rank, expected):
    before = fingerprint(values)
    assert_numeric(generated_log10(rank)(values).log_values, expected)
    assert fingerprint(values) == before


@pytest.mark.parametrize("terminal", ["input", "output"])
@pytest.mark.parametrize("underlying", ["NumComplex128", None])
def test_unqualified_representation_is_rejected(terminal, underlying):
    with pytest.raises(
        CodeGenError, match="supports resolved real numeric scalars and arrays only"
    ):
        generated_log10(**{f"{terminal}_type": underlying})


@pytest.mark.parametrize("input_type", ["NumFloat32", "NumFloatExt", "NumInt32"])
def test_other_real_inputs_generate(input_type):
    assert generated_log10(input_type=input_type)(100).log_values == 2.0


@pytest.mark.parametrize(
    "values,expected",
    [
        ([], []),
        ([1.0, 10.0], [0.0, 1.0]),
        ([[0.0, -1.0], [1.0, math.inf]], [[-math.inf, math.nan], [0.0, math.inf]]),
    ],
)
def test_runtime_log10_handles_lists_without_mutation(values, expected):
    before = fingerprint(values)
    assert_numeric(lv.log10(values), expected)
    assert fingerprint(values) == before
