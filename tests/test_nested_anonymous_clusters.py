"""Execute anonymous nMux reads and updates using saved depth-first indices."""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from functools import cache
from types import SimpleNamespace

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.nodes import nmux
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.models import (
    ClusterField,
    FPTerminal,
    LVType,
    LVTypeKind,
    Terminal,
    TypeResolutionNeeded,
)
from tests.helpers import build_graph, make_ctx

DBL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")


def cluster(*fields):
    return LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name=name, type=kind) for name, kind in fields],
    )


INNER = cluster(("left", DBL), ("right", DBL))
GRAND = cluster(("right", DBL), ("tail", DBL))
DEEP_INNER = cluster(("left", DBL), ("grand", GRAND))


@dataclass(frozen=True)
class Example:
    name: str
    lv_type: LVType
    value: tuple
    field_types: tuple[LVType, ...]
    field_values: tuple


EXAMPLES = (
    Example(
        "trailing",
        cluster(("marker", DBL), ("inner", INNER)),
        (10.0, (20.0, 30.0)),
        (DBL, INNER, DBL, DBL),
        (10.0, (20.0, 30.0), 20.0, 30.0),
    ),
    Example(
        "leading",
        cluster(("inner", INNER), ("marker", DBL)),
        ((20.0, 30.0), 10.0),
        (INNER, DBL, DBL, DBL),
        ((20.0, 30.0), 20.0, 30.0, 10.0),
    ),
    Example(
        "deep",
        cluster(("marker", DBL), ("inner", DEEP_INNER)),
        (10.0, (20.0, (30.0, 40.0))),
        (DBL, DEEP_INNER, DBL, GRAND, DBL, DBL),
        (10.0, (20.0, (30.0, 40.0)), 20.0, (30.0, 40.0), 30.0, 40.0),
    ),
)


def example(name):
    return next(item for item in EXAMPLES if item.name == name)


def public_terminal(name, direction, kind, index=0):
    return FPTerminal(
        id=f"fp:{name}",
        index=index,
        direction=direction,
        name=name,
        is_public=True,
        is_indicator=direction == "output",
        lv_type=kind,
    )


def compile_vi(node, inputs, output, pairs):
    context = VIContext(
        name="Cluster Test.vi",
        inputs=inputs,
        outputs=[output],
        data_flow=[
            Wire.from_terminals(from_terminal_id=a, to_terminal_id=b) for a, b in pairs
        ],
    )
    graph = build_graph(context, context.name, [node])
    source = build_module(context, context.name, graph=graph)
    namespace = {}
    exec(compile(source, context.name, "exec"), namespace)
    return namespace["cluster_test"]


@cache
def generated_unbundle(shape, field_index):
    item = example(shape)
    kind = (
        item.field_types[field_index]
        if field_index is not None and 0 <= field_index < len(item.field_types)
        else DBL
    )
    node = PrimitiveNode(
        id="unbundle",
        vi_path="Cluster Test.vi",
        node_type="nMux",
        terminals=[
            Terminal(
                id="agg",
                index=0,
                direction="input",
                nmux_role="agg",
                lv_type=item.lv_type,
            ),
            Terminal(
                id="field",
                index=1,
                direction="output",
                nmux_role="list",
                nmux_field_index=field_index,
                lv_type=kind,
            ),
        ],
    )
    return compile_vi(
        node,
        [public_terminal("cluster_in", "input", item.lv_type)],
        public_terminal("value", "output", kind),
        [("fp:cluster_in", "agg"), ("field", "fp:value")],
    )


@cache
def generated_bundle(shape, indices):
    item = example(shape)
    node = PrimitiveNode(
        id="bundle",
        vi_path="Cluster Test.vi",
        node_type="nMux",
        terminals=[
            Terminal(
                id="agg_out",
                index=0,
                direction="output",
                nmux_role="agg",
                lv_type=item.lv_type,
            ),
            Terminal(
                id="agg_in",
                index=1,
                direction="input",
                nmux_role="agg",
                lv_type=item.lv_type,
            ),
        ],
    )
    inputs = [public_terminal("cluster_in", "input", item.lv_type)]
    pairs = [("fp:cluster_in", "agg_in"), ("agg_out", "fp:value")]
    for row, field_index in enumerate(indices):
        kind = item.field_types[field_index]
        node.terminals.append(
            Terminal(
                id=f"field{row}",
                index=row + 2,
                direction="input",
                nmux_role="list",
                nmux_field_index=field_index,
                lv_type=kind,
            )
        )
        inputs.append(public_terminal(f"replacement_{row}", "input", kind, row + 1))
        pairs.append((f"fp:replacement_{row}", f"field{row}"))
    return compile_vi(
        node, inputs, public_terminal("value", "output", item.lv_type), pairs
    )


@pytest.mark.parametrize(
    "shape,index,expected",
    [
        (item.name, index, value)
        for item in EXAMPLES
        for index, value in enumerate(item.field_values)
    ],
)
def test_depth_first_unbundle(shape, index, expected):
    item = example(shape)
    assert generated_unbundle(shape, index)(item.value).value == expected


@pytest.mark.parametrize(
    "shape,indices,replacements,expected",
    [
        ("trailing", (0,), (99.0,), (99.0, (20.0, 30.0))),
        ("trailing", (2,), (99.0,), (10.0, (99.0, 30.0))),
        ("trailing", (3,), (99.0,), (10.0, (20.0, 99.0))),
        ("trailing", (1,), ((99.0, 100.0),), (10.0, (99.0, 100.0))),
        ("trailing", (2, 3), (99.0, 100.0), (10.0, (99.0, 100.0))),
        ("leading", (3,), (99.0,), ((20.0, 30.0), 99.0)),
        ("deep", (4,), (99.0,), (10.0, (20.0, (99.0, 40.0)))),
        ("deep", (3,), ((99.0, 100.0),), (10.0, (20.0, (99.0, 100.0)))),
    ],
)
def test_bundle_preserves_input(shape, indices, replacements, expected):
    item = example(shape)
    before = copy.deepcopy(item.value)
    result = generated_bundle(shape, indices)(item.value, *replacements).value
    assert result == expected
    assert item.value == before
    assert result is not item.value


@pytest.mark.parametrize("index", [None, -1, 99])
def test_missing_or_invalid_field_indices_fail(index):
    with pytest.raises(TypeResolutionNeeded, match="positional field"):
        generated_unbundle("trailing", index)


@pytest.mark.parametrize("operation", ["read", "write"])
def test_unknown_fields_fail_instead_of_guessing(operation):
    unknown = LVType(kind=LVTypeKind.CLUSTER)
    direction = "output" if operation == "read" else "input"
    node = PrimitiveNode(
        id="unknown",
        vi_path="test.vi",
        node_type="nMux",
        terminals=[
            Terminal(
                id="agg", index=0, direction="input", nmux_role="agg", lv_type=unknown
            ),
            Terminal(
                id="field",
                index=1,
                direction=direction,
                nmux_role="list",
                nmux_field_index=0,
                lv_type=DBL,
            ),
        ],
    )
    context = make_ctx("agg", "field")
    context.bind("agg", "incoming")
    context.bind("field", "replacement")
    with pytest.raises(TypeResolutionNeeded):
        nmux.generate(node, context)


def run_fragment(node, incoming):
    context = make_ctx(*(term.id for term in node.terminals))
    context.bind("agg", "incoming")
    context.bind("field", "99.0")
    fragment = nmux.generate(node, context)
    module = ast.fix_missing_locations(
        ast.Module(body=fragment.statements, type_ignores=[])
    )
    namespace = {"incoming": incoming}
    exec(compile(module, "<named-cluster>", "exec"), namespace)
    return fragment, namespace


@pytest.mark.parametrize("operation", ["read", "write"])
def test_named_cluster_attribute_path_is_unchanged(operation):
    named = cluster(("marker", DBL))
    named.typedef_name = "NamedCluster"
    node = PrimitiveNode(
        id="named",
        vi_path="test.vi",
        node_type="nMux",
        terminals=[
            Terminal(
                id="agg", index=0, direction="input", nmux_role="agg", lv_type=named
            ),
            Terminal(
                id="result", index=1, direction="output", nmux_role="agg", lv_type=named
            ),
            Terminal(
                id="field",
                index=2,
                direction="output" if operation == "read" else "input",
                nmux_role="list",
                nmux_field_index=0,
                lv_type=DBL,
            ),
        ],
    )
    incoming = SimpleNamespace(marker=10.0)
    fragment, namespace = run_fragment(node, incoming)
    if operation == "read":
        assert eval(fragment.bindings["field"], namespace) == 10.0
    else:
        assert incoming.marker == 99.0
        assert eval(fragment.bindings["result"], namespace) is incoming


def test_anonymous_path_does_not_guess_inside_named_descendant():
    named = cluster(("child", DBL))
    named.typedef_name = "NamedChild"
    outer = cluster(("named", named), ("last", DBL))
    node = PrimitiveNode(
        id="mixed",
        vi_path="test.vi",
        node_type="nMux",
        terminals=[
            Terminal(
                id="agg", index=0, direction="input", nmux_role="agg", lv_type=outer
            ),
            Terminal(
                id="field",
                index=1,
                direction="output",
                nmux_role="list",
                nmux_field_index=1,
                lv_type=DBL,
            ),
        ],
    )
    with pytest.raises(TypeResolutionNeeded, match="positional field"):
        run_fragment(node, (SimpleNamespace(child=10.0), 20.0))
    node.terminals[1].nmux_field_index = 2
    fragment, namespace = run_fragment(node, (SimpleNamespace(child=10.0), 20.0))
    assert eval(fragment.bindings["field"], namespace) == 20.0


def test_unresolved_bundle_value_fails_explicitly():
    node = PrimitiveNode(
        id="missing",
        vi_path="test.vi",
        node_type="nMux",
        terminals=[
            Terminal(
                id="agg",
                index=0,
                direction="input",
                nmux_role="agg",
                lv_type=example("trailing").lv_type,
            ),
            Terminal(
                id="field",
                index=1,
                direction="input",
                nmux_role="list",
                nmux_field_index=3,
                lv_type=DBL,
            ),
        ],
    )
    context = make_ctx("agg", "field")
    context.bind("agg", "incoming")
    with pytest.raises(TypeResolutionNeeded, match="bundle value"):
        nmux.generate(node, context)
