"""Execute generated 1D Index Array rows and guard the rank boundary."""

from __future__ import annotations

import ast
from functools import cache

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.nodes import primitive
from lvkit.codegen.nodes.base import CodeGenError
from lvkit.graph.models import PrimitiveNode, VIContext, Wire, WireEnd
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.runtime import lv
from tests.helpers import build_graph, make_ctx

DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")
I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def index_node(rows=3, *, element=DBL, rank=1):
    array = LVType(kind=LVTypeKind.ARRAY, element_type=element, dimensions=rank)
    terminals = [Terminal(id="array", index=0, direction="input", lv_type=array)]
    for row in range(rows):
        terminals.extend(
            [
                Terminal(
                    id=f"out{row}",
                    index=2 * row + 1,
                    direction="output",
                    lv_type=element,
                ),
                Terminal(
                    id=f"idx{row}", index=2 * row + 2, direction="input", lv_type=I32
                ),
            ]
        )
    return PrimitiveNode(
        id="index",
        vi_path="Index Rows Test.vi",
        name="Index Array",
        node_type="aIndx",
        terminals=terminals,
    )


@cache
def generated_index(
    *,
    indices=(True, False, False),
    outputs=(True, True, True),
    array_wired=True,
    underlying="NumFloat64",
    rank=1,
):
    element = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=underlying)
    node = index_node(len(indices), element=element, rank=rank)
    array_type = node.terminals[0].lv_type
    inputs, results, pairs = [], [], []
    if array_wired:
        inputs.append(
            FPTerminal(
                id="fp:values",
                index=0,
                direction="input",
                name="values",
                is_public=True,
                is_indicator=False,
                lv_type=array_type,
            )
        )
        pairs.append(("fp:values", "array"))
    for row, wired in enumerate(indices):
        if wired:
            inputs.append(
                FPTerminal(
                    id=f"fp:idx{row}",
                    index=len(inputs),
                    direction="input",
                    name=f"index_{row}",
                    is_public=True,
                    is_indicator=False,
                    lv_type=I32,
                )
            )
            pairs.append((f"fp:idx{row}", f"idx{row}"))
    for row, wired in enumerate(outputs):
        if wired:
            results.append(
                FPTerminal(
                    id=f"fp:out{row}",
                    index=len(results),
                    direction="output",
                    name=f"element_{row}",
                    is_public=True,
                    is_indicator=True,
                    lv_type=element,
                )
            )
            pairs.append((f"out{row}", f"fp:out{row}"))
    context = VIContext(
        name=node.vi_path,
        inputs=inputs,
        outputs=results,
        data_flow=[
            Wire.from_terminals(from_terminal_id=a, to_terminal_id=b) for a, b in pairs
        ],
    )
    graph = build_graph(context, context.name, [node])
    source = build_module(context, context.name, graph=graph)
    namespace = {}
    exec(compile(source, context.name, "exec"), namespace)
    return namespace["index_rows_test"]


@pytest.mark.parametrize(
    "values,start,expected",
    [
        ([], 0, (0.0, 0.0, 0.0)),
        ([10.0], 0, (10.0, 0.0, 0.0)),
        ([10.0, 20.0, 30.0], 0, (10.0, 20.0, 30.0)),
        ([10.0, 20.0, 30.0, 40.0], 1, (20.0, 30.0, 40.0)),
        ([10.0, 20.0, 30.0], 2, (30.0, 0.0, 0.0)),
        ([10.0, 20.0, 30.0], 3, (0.0, 0.0, 0.0)),
        ([10.0, 20.0, 30.0], 100, (0.0, 0.0, 0.0)),
        ([10.0, 20.0, 30.0], -1, (0.0, 10.0, 20.0)),
        ([10.0, 20.0, 30.0], -2, (0.0, 0.0, 10.0)),
        ([10.0, 20.0, 30.0], -100, (0.0, 0.0, 0.0)),
    ],
)
def test_expanded_rows(values, start, expected):
    before = values[:]
    assert tuple(generated_index()(values, start)) == expected
    assert values == before


def test_unwired_first_index_starts_at_zero():
    function = generated_index(indices=(False, False, False))
    assert tuple(function([10.0, 20.0, 30.0])) == (10.0, 20.0, 30.0)


def test_wired_middle_index_restarts_progression():
    function = generated_index(indices=(True, True, False))
    assert tuple(function([10.0, 20.0, 30.0, 40.0, 50.0], 1, 3)) == (20.0, 40.0, 50.0)


def test_wired_last_index_selects_its_own_row():
    function = generated_index(indices=(True, False, True))
    assert tuple(function([10.0, 20.0, 30.0], 1, 0)) == (20.0, 30.0, 10.0)


@pytest.mark.parametrize(
    "outputs,expected",
    [
        ((False, False, True), (30.0,)),
        ((True, False, True), (10.0, 30.0)),
        ((False, True, True), (20.0, 30.0)),
    ],
)
def test_unwired_outputs_still_advance_indices(outputs, expected):
    result = generated_index(outputs=outputs)([10.0, 20.0, 30.0], 0)
    assert tuple(result) == expected


def test_wired_index_on_unwired_output_restarts_progression():
    function = generated_index(indices=(True, True, False), outputs=(True, False, True))
    assert tuple(function([10.0, 20.0, 30.0, 40.0, 50.0], 0, 3)) == (10.0, 50.0)


def test_unwired_array_has_empty_type_default():
    assert tuple(generated_index(array_wired=False)(0)) == (0.0, 0.0, 0.0)


def test_omitted_public_array_and_index_defaults():
    assert tuple(generated_index()()) == (0.0, 0.0, 0.0)


@pytest.mark.parametrize(
    "underlying,values,default",
    [
        ("NumInt32", [10, 20, 30], 0),
        ("NumUInt8", [10, 20, 30], 0),
        ("Boolean", [True, False, True], False),
        ("String", ["a", "b", "c"], ""),
    ],
)
def test_scalar_element_defaults_keep_their_types(underlying, values, default):
    function = generated_index(underlying=underlying)
    assert tuple(function(values, 0)) == tuple(values)
    result = function(values, 3)
    assert tuple(result) == (default,) * 3
    assert all(type(value) is type(default) for value in result)


def wire_context(node, *, resolved=True):
    context = make_ctx(*(term.id for term in node.terminals), "sink")
    graph = context.graph
    for term in node.terminals[1::2]:
        graph._graph.add_edge(
            graph._term_to_node[term.id],
            graph._term_to_node["sink"],
            source=WireEnd(node_id=graph._term_to_node[term.id], terminal_id=term.id),
            dest=WireEnd(node_id=graph._term_to_node["sink"], terminal_id="sink"),
        )
    if resolved:
        context.bind("array", "get_values()")
    return context


def test_array_expression_evaluated_once():
    node = index_node()
    context = wire_context(node)
    fragment = primitive.generate(node, context)
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    calls = []

    def get_values():
        calls.append(1)
        return [10.0, 20.0, 30.0]

    namespace = {"_lv": lv, "get_values": get_values}
    exec(compile(module, "<expanded-index>", "exec"), namespace)
    assert [namespace[fragment.bindings[f"out{row}"]] for row in range(3)] == [
        10.0,
        20.0,
        30.0,
    ]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "rank,values,expected,default",
    [
        (None, [10.0], 10.0, 0.0),
        (2, [[10.0, 20.0], [30.0, 40.0]], [10.0, 20.0], []),
        (3, [[[10.0], [20.0]], [[30.0], [40.0]]], [[10.0], [20.0]], []),
    ],
)
def test_other_ranks_keep_the_single_output_template(rank, values, expected, default):
    node = index_node(1, rank=rank)
    if rank is not None:
        node.terminals[1].lv_type = LVType(
            kind=LVTypeKind.ARRAY, element_type=DBL, dimensions=rank - 1
        )
    context = wire_context(node)
    context.bind("array", "values")
    context.bind("idx0", "start")
    fragment = primitive.generate(node, context)
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    for start, target in ((0, expected), (99, default)):
        namespace = {"_lv": lv, "values": values, "start": start}
        exec(compile(module, "<index-rank-boundary>", "exec"), namespace)
        assert namespace[fragment.bindings["out0"]] == target


@pytest.mark.parametrize(
    "invalid",
    ["missing_index", "wrong_direction", "missing_output_type", "array_output"],
)
def test_invalid_saved_rows_fail_explicitly(invalid):
    node = index_node()
    if invalid == "missing_index":
        node.terminals.pop()
    elif invalid == "wrong_direction":
        node.terminals[2].direction = "output"
    elif invalid == "missing_output_type":
        node.terminals[1].lv_type = None
    else:
        node.terminals[1].lv_type = node.terminals[0].lv_type
    with pytest.raises(CodeGenError, match="1D Index Array"):
        primitive.generate(node, wire_context(node))


@pytest.mark.parametrize("terminal", ["array", "idx0"])
def test_unresolved_wired_inputs_fail_explicitly(terminal):
    node = index_node()
    context = wire_context(node, resolved=terminal != "array")
    graph = context.graph
    graph._graph.add_edge(
        graph._term_to_node["sink"],
        graph._term_to_node[terminal],
        source=WireEnd(node_id=graph._term_to_node["sink"], terminal_id="sink"),
        dest=WireEnd(node_id=graph._term_to_node[terminal], terminal_id=terminal),
    )
    with pytest.raises(CodeGenError, match="Unresolved wired 1D Index Array"):
        primitive.generate(node, context)
