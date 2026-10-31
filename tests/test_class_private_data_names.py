"""Class private-data fields: a label is kept as written; no label names the
field by its position in the cluster."""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET

from lvkit.codegen.class_builder import ClassBuilder
from lvkit.codegen.class_model import class_model
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.node_kinds import NodeType
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.structure import _resolve_type_ids


def _vctp(*labels: str) -> ET.Element:
    descs = "".join(f'<TypeDesc Type="String" Label="{label}"/>' for label in labels)
    return ET.fromstring(f"<R><VCTP><Section>{descs}</Section></VCTP></R>")


def test_label_none_is_a_field_and_unlabelled_gets_position_name() -> None:
    root = _vctp("None", "", "Count")
    fields = _resolve_type_ids(root, ["0", "1", "2"])
    assert [f.name for f in fields] == ["None", "field_1", "Count"]


def test_init_keeps_every_field() -> None:
    graph = InMemoryVIGraph()
    string = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
    number = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
    graph._dep_graph.add_node(  # noqa: SLF001
        "/src/C.lvclass",
        node_type=NodeType.CLASS,
        fields=[
            ClusterField(name="None", type=string),
            ClusterField(name="field_1", type=number),
        ],
        parent_class="LabVIEW Object",
    )
    init = ClassBuilder()._build_init(  # noqa: SLF001
        class_model(graph, "/src/C.lvclass"),
        graph.class_own_fields("/src/C.lvclass"),
        False,
        None,
    )
    targets = [
        stmt.targets[0].attr  # type: ignore[attr-defined]
        for stmt in init.body
        if isinstance(stmt, ast.Assign)
    ]
    assert targets == ["_none", "_field_1"]
    assert [ast.unparse(s.value) for s in init.body if isinstance(s, ast.Assign)] == [
        "''",
        "0",
    ]


def test_position_name_never_repeats_a_labelled_field() -> None:
    # The unlabelled field at position 1 would be ``field_1``; a labelled field
    # of that name (before it or after it) keeps it.
    for labels in (["field_1", "", "Count"], ["", "Count", "field_0"]):
        root = _vctp(*labels)
        names = [f.name for f in _resolve_type_ids(root, ["0", "1", "2"])]
        assert len(set(names)) == 3
    root = _vctp("field_1", "", "")
    names = [f.name for f in _resolve_type_ids(root, ["0", "1", "2"])]
    assert names == ["field_1", "field_1_2", "field_2"]
    root = _vctp("", "field_0")
    names = [f.name for f in _resolve_type_ids(root, ["0", "1"])]
    assert names == ["field_0_2", "field_0"]
