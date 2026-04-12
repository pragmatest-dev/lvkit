"""Tests for get_type_fields / get_class_fields on InMemoryVIGraph."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.models import ClusterField, LVType

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TESTCASE_LVCLASS = (
    SAMPLES / "JKI-VI-Tester" / "source" / "Classes" / "TestCase" / "TestCase.lvclass"
)


class TestGetTypeFields:
    """get_type_fields routes to dep_graph for named types, inline for anonymous."""

    def test_class_fields_from_dep_graph(self):
        graph = InMemoryVIGraph()
        float_type = LVType(kind="primitive", underlying_type="NumFloat")
        fields = [
            ClusterField(name="x", type=float_type),
            ClusterField(name="y", type=float_type),
        ]
        graph._dep_graph.add_node("Foo.lvclass", node_type="class", fields=fields)

        result = graph.get_type_fields(LVType(kind="class", classname="Foo.lvclass"))
        assert result == fields

    def test_typedef_fields_from_dep_graph(self):
        graph = InMemoryVIGraph()
        fields = [
            ClusterField(
                name="status",
                type=LVType(kind="primitive", underlying_type="Boolean"),
            ),
        ]
        graph._dep_graph.add_node("Bar.ctl", node_type="typedef", fields=fields)

        result = graph.get_type_fields(LVType(kind="cluster", typedef_name="Bar.ctl"))
        assert result == fields

    def test_inline_cluster_fields(self):
        graph = InMemoryVIGraph()
        inline_fields = [
            ClusterField(
                name="a",
                type=LVType(kind="primitive", underlying_type="String"),
            ),
        ]
        lv_type = LVType(kind="cluster", fields=inline_fields)

        result = graph.get_type_fields(lv_type)
        assert result == inline_fields

    def test_unknown_named_type_returns_none(self):
        graph = InMemoryVIGraph()
        result = graph.get_type_fields(
            LVType(kind="class", classname="Unknown.lvclass"),
        )
        assert result is None


@pytest.mark.skipif(
    not TESTCASE_LVCLASS.exists(),
    reason="JKI-VI-Tester samples not available",
)
class TestGetClassFieldsFromRealFile:
    def test_load_lvclass_populates_fields(self):
        graph = InMemoryVIGraph()
        graph.load_lvclass(TESTCASE_LVCLASS, expand_subvis=False)

        # TestCase.lvclass should be in dep_graph
        class_nodes = [
            n for n in graph._dep_graph.nodes
            if n.endswith("TestCase.lvclass")
        ]
        assert len(class_nodes) >= 1

        fields = graph.get_class_fields(class_nodes[0])
        assert fields is not None
        assert len(fields) > 0
        # Fields should have names (not empty strings)
        for f in fields:
            assert f.name
