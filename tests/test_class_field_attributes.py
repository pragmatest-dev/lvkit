"""Private-data fields whose labels become one Python name each get their own
storage in the generated class, ``__<field>`` (private to the class)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lvkit.codegen.class_model import class_model
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
    return names, class_model(graph, key).attributes.fields


@pytest.mark.parametrize(
    ("labels", "labelled", "attributes"),
    [
        (("", "Field 0"), ["field_0", "Field 0"], ["__field_0", "__field_0_2"]),
        (("Count", "count"), ["Count", "count"], ["__count", "__count_2"]),
        (("a b", "a_b"), ["a b", "a_b"], ["__a_b", "__a_b_2"]),
        (("x", "y"), ["x", "y"], ["__x", "__y"]),
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
    assert class_model(graph, key).attributes.fields == ["__count", "__count_2"]


def test_a_child_field_of_a_parents_name_is_stored_by_its_own_class() -> None:
    graph, parent = _class_graph(["Count"])
    graph._dep_graph.add_node(  # noqa: SLF001
        "/src/D.lvclass",
        node_type=NodeType.CLASS,
        fields=[ClusterField(name="count", type=_INT)],
        parent_class="C",
        parent_key=parent,
    )
    # Python stores each with its class's name: `_C__count` and `_D__count`.
    assert class_model(graph, "/src/D.lvclass").attributes.fields == ["__count"]
