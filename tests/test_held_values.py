"""The graph build marks an output whose value its node's input still holds
(``Terminal.held_in``): an Unbundle By Name or In Place Element field, an Index
Array element, the value of Get Variant Attribute and the data of Variant To Data
(the catalog's ``part_of``, in either terminal numbering of Variant To Data). Each
case is a tiny diagram read back from the graph."""

from __future__ import annotations

import pytest

from lvkit.graph.held_values import mark_held_outputs
from lvkit.graph.models import AnyGraphNode
from lvkit.primitive_resolver import PrimitiveResolver
from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    REQUIRED,
    STR,
    Project,
    Vi,
    bundle,
    project,  # noqa: F401
    unbundle,
)
from tests.test_expanded_index_array import index_node
from tests.test_value_shared_container import ARRAY_1D, ARRAY_2D

NAME = "Held.vi"
ADD = 1050


def _nodes(project: Project) -> list[AnyGraphNode]:  # noqa: F811
    views = project.views(NAME)
    graph = views.graph
    return [
        graph.get_graph_node(uid)
        for uid in sorted(graph._vi_nodes[views.vi])
        if graph.get_graph_node(uid) is not None
    ]


def _held(node: AnyGraphNode) -> dict[int, str | None]:
    return {t.index: t.held_in for t in node.terminals}


def test_an_index_array_element_is_held_by_its_array(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    row_type = vi.add_type(ARRAY_1D)
    grid_type = vi.add_type(ARRAY_2D)
    values = vi.control("values", grid_type, REQUIRED)
    index = vi.index_array(grid_type, row_type)
    vi.wire(values, index["array"])
    vi.wire(vi.int_constant(0), index["index"])
    vi.wire(index["element"], vi.indicator("row", row_type))
    project.add(vi)
    (node,) = [n for n in _nodes(project) if n.node_type == "aIndx"]
    array = next(t for t in node.terminals if t.index == 0)
    assert _held(node) == {0: None, 1: array.id, 2: None}


def test_every_element_of_a_resized_index_array_is_held_by_its_array() -> None:
    node = index_node(2)
    entry = PrimitiveResolver().resolve_by_node_type("aIndx")
    assert entry is not None
    mark_held_outputs(node, entry.terminals)
    assert {t.id: t.held_in for t in node.terminals} == {
        "array": None,
        "out0": "array",
        "idx0": None,
        "out1": "array",
        "idx1": None,
    }


def test_every_field_an_unbundle_by_name_gives_is_held_by_its_cluster(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    node = unbundle(vi, [("status", 0), ("code", 1)])
    vi.wire(vi.error_in(), node["cluster"])
    vi.wire(node["status"], vi.indicator("s", BOOL))
    vi.wire(node["code"], vi.indicator("c", I32))
    project.add(vi)
    (unbundled,) = [n for n in _nodes(project) if n.node_type == "nMux"]
    cluster = next(t for t in unbundled.terminals if t.nmux_role == "agg")
    outputs = [t for t in unbundled.terminals if t.direction == "output"]
    assert len(outputs) == 2
    assert [t.held_in for t in outputs] == [cluster.id, cluster.id]
    assert cluster.held_in is None


def test_nothing_a_bundle_by_name_has_is_held(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    node = bundle(vi, [("status", 0), ("code", 1)])
    vi.wire(vi.error_in(), node["cluster in"])
    vi.wire(vi.bool_constant(True), node["status"])
    vi.wire(vi.int_constant(1), node["code"])
    vi.wire(node["cluster out"], vi.error_out())
    project.add(vi)
    (bundled,) = [n for n in _nodes(project) if n.node_type == "nMux"]
    assert [t.held_in for t in bundled.terminals] == [None] * len(bundled.terminals)


def test_a_primitive_whose_catalog_has_no_part_of_holds_nothing(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    add = vi.primitive(
        ADD, [("sum", 0, I32, True), ("y", 1, I32, False), ("x", 2, I32, False)]
    )
    vi.wire(vi.int_constant(1), add["x"], add["y"])
    vi.wire(add["sum"], vi.indicator("total", I32))
    project.add(vi)
    (node,) = [n for n in _nodes(project) if n.name == "Add"]
    assert [t.held_in for t in node.terminals] == [None, None, None]


_VARIANT = '<TypeDesc Type="Variant" Format="inline" />'


def test_the_value_of_get_variant_attribute_is_held_by_its_variant(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(
        8205,
        [
            ("error out", 0, ERR, True),
            ("value", 1, variant_type, True),
            ("found", 2, BOOL, True),
            ("duplicate", 3, variant_type, True),
            ("error in", 4, ERR, False),
            ("default", 5, variant_type, False),
            ("name", 6, STR, False),
            ("variant", 7, variant_type, False),
        ],
    )
    vi.wire(vi.control("v", variant_type, REQUIRED), node["variant"])
    vi.wire(node["value"], vi.indicator("value", variant_type))
    project.add(vi)
    (get,) = [n for n in _nodes(project) if n.name == "Get Variant Attribute"]
    variant = next(t for t in get.terminals if t.index == 7)
    held = _held(get)
    assert held[1] == variant.id
    assert [i for i, held_in in held.items() if held_in is not None] == [1]


@pytest.mark.parametrize(
    "indices",
    [
        # (error in, error out, variant, type, data) in the two numberings
        (0, 3, 8, 9, 11),
        (0, 1, 2, 3, 4),
    ],
)
def test_the_data_of_variant_to_data_is_held_by_its_variant_in_either_numbering(
    project: Project,  # noqa: F811
    indices: tuple[int, int, int, int, int],
) -> None:
    error_in, error_out, variant_in, type_in, data_out = indices
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(
        8003,
        [
            ("error in", error_in, ERR, False),
            ("error out", error_out, ERR, True),
            ("variant", variant_in, variant_type, False),
            ("type", type_in, I32, False),
            ("data", data_out, I32, True),
        ],
    )
    vi.wire(vi.control("v", variant_type, REQUIRED), node["variant"])
    vi.wire(node["data"], vi.indicator("d", I32))
    project.add(vi)
    (convert,) = [n for n in _nodes(project) if n.name == "Variant To Data"]
    variant = next(t for t in convert.terminals if t.index == variant_in)
    held = _held(convert)
    assert held[data_out] == variant.id
    assert [i for i, held_in in held.items() if held_in is not None] == [data_out]
