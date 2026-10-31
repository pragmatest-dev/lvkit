"""Private-data fields whose labels become one Python name each get their own
attribute in the generated class."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lvkit.codegen.class_attributes import class_attributes
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.node_kinds import NodeType
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.structure import _resolve_type_ids

_INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def _class_graph(names: list[str]) -> tuple[InMemoryVIGraph, str]:
    graph = InMemoryVIGraph()
    graph._dep_graph.add_node(  # noqa: SLF001
        "/src/C.lvclass",
        node_type=NodeType.CLASS,
        fields=[ClusterField(name=n, type=_INT) for n in names],
        parent_class="LabVIEW Object",
    )
    return graph, "/src/C.lvclass"


def _attributes(labels: tuple[str, ...]) -> tuple[list[str], list[str]]:
    descs = "".join(f'<TypeDesc Type="String" Label="{lb}"/>' for lb in labels)
    root = ET.fromstring(f"<R><VCTP><Section>{descs}</Section></VCTP></R>")
    fields = _resolve_type_ids(root, [str(i) for i in range(len(labels))])
    names = [f.name for f in fields]
    graph, key = _class_graph(names)
    return names, class_attributes(graph, key).fields


@pytest.mark.parametrize(
    ("labels", "labelled", "attributes"),
    [
        (("", "Field 0"), ["field_0", "Field 0"], ["_field_0", "_field_0_2"]),
        (("Count", "count"), ["Count", "count"], ["_count", "_count_2"]),
        (("a b", "a_b"), ["a b", "a_b"], ["_a_b", "_a_b_2"]),
        (("x", "y"), ["x", "y"], ["_x", "_y"]),
    ],
)
def test_each_field_gets_its_own_attribute(
    labels: tuple[str, ...], labelled: list[str], attributes: list[str]
) -> None:
    names, attrs = _attributes(labels)
    assert names == labelled
    assert attrs == attributes


def test_a_field_no_accessor_names_is_private() -> None:
    graph, key = _class_graph(["Count", "count"])
    assert class_attributes(graph, key).fields == ["_count", "_count_2"]


def test_a_child_field_never_takes_the_attribute_of_its_parents() -> None:
    graph, parent = _class_graph(["Count"])
    graph._dep_graph.add_node(  # noqa: SLF001
        "/src/D.lvclass",
        node_type=NodeType.CLASS,
        fields=[ClusterField(name="count", type=_INT)],
        parent_class="C",
        parent_key=parent,
    )
    assert class_attributes(graph, "/src/D.lvclass").fields == ["_count_2"]
