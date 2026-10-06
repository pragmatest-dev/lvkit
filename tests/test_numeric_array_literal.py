"""Numeric Add must preserve element-wise semantics for literal operands."""

from __future__ import annotations

import ast
import copy

import pytest

from lvkit.codegen.elementwise import arrayify, arrayify_module
from lvkit.codegen.nodes import primitive
from lvkit.graph.models import PrimitiveNode, WireEnd
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.runtime import lv
from tests.helpers import make_ctx


def execute_expression(expression, **values):
    generated, _ = arrayify(ast.parse(expression, mode="eval").body)
    return eval(
        compile(ast.Expression(generated), "<numeric-add>", "eval"),
        {"__builtins__": {}, "_lv": lv, **values},
    )


@pytest.mark.parametrize(
    "expression",
    ["bank + [0.0, -2.0, -2.0]", "[0.0, -2.0, -2.0] + bank"],
)
@pytest.mark.parametrize(
    "bank,expected",
    [
        ([1.0, 4.0, 5.0], [1.0, 2.0, 3.0]),
        ([], []),
        ([9.0], [9.0]),
        ([1.0, 4.0, 5.0, 99.0], [1.0, 2.0, 3.0]),
    ],
)
def test_literal_add_values_shape_and_input(expression, bank, expected):
    original = copy.deepcopy(bank)
    assert execute_expression(expression, bank=bank) == expected
    assert bank == original


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("[1, 2] + [3, 4]", [4, 6]),
        ("[[1, 2], [3, 4]] + [[10, 20], [30, 40]]", [[11, 22], [33, 44]]),
        ("2.0 + [1.0, 3.0]", [3.0, 5.0]),
        ("[1.0, 3.0] + 2.0", [3.0, 5.0]),
    ],
)
def test_literal_and_scalar_broadcast(expression, expected):
    assert execute_expression(expression) == expected


def test_typed_add_primitive_emits_executable_elementwise_code():
    array = LVType(
        kind=LVTypeKind.ARRAY,
        dimensions=1,
        element_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64"),
    )
    node = PrimitiveNode(
        id="add",
        vi_path="array_add_literal.vi",
        name="Add",
        prim_id=1050,
        node_type="prim",
        terminals=[
            Terminal(id="out", index=0, direction="output", lv_type=array),
            Terminal(id="literal", index=1, direction="input", lv_type=array),
            Terminal(id="bank", index=2, direction="input", lv_type=array),
        ],
    )
    ctx = make_ctx("bank", "literal", "out", "sink")
    ctx.graph._graph.add_edge(
        ctx.graph._term_to_node["out"],
        ctx.graph._term_to_node["sink"],
        source=WireEnd(terminal_id="out", node_id=ctx.graph._term_to_node["out"]),
        dest=WireEnd(terminal_id="sink", node_id=ctx.graph._term_to_node["sink"]),
    )
    ctx.bind("bank", "bank")
    ctx.bind("literal", "[0.0, -2.0, -2.0]")
    fragment = primitive.generate(node, ctx)
    namespace = {"_lv": lv, "bank": [1.0, 4.0, 5.0]}
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    exec(compile(module, "<typed-add>", "exec"), namespace)
    assert namespace[fragment.bindings["out"]] == [1.0, 2.0, 3.0]
    assert namespace["bank"] == [1.0, 4.0, 5.0]


@pytest.mark.parametrize(
    "statement,values,expected",
    [
        ("result = bank + [new]", {"bank": [1, 2], "new": 3}, [1, 2, 3]),
        (
            "result = bank[:1] + [new] + bank[2:]",
            {"bank": [1, 2, 3], "new": 9},
            [1, 9, 3],
        ),
    ],
)
def test_module_build_and_replace_concatenation(statement, values, expected):
    body = ast.parse(statement).body
    arrayify_module(body, frozenset({"bank"}))
    namespace = {"_lv": lv, **values}
    module = ast.fix_missing_locations(ast.Module(body=body, type_ignores=[]))
    exec(compile(module, "<array-concat>", "exec"), namespace)
    assert namespace["result"] == expected
