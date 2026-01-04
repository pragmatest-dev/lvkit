"""Regression tests for parser refactoring."""

import pytest
from pathlib import Path
from vipy.parser.block_diagram import parse_block_diagram
from vipy.parser.types import parse_type_map_rich
from vipy.parser.metadata import parse_vi_metadata, parse_subvi_paths
from vipy.memory_graph import InMemoryVIGraph
from vipy.extractor import extract_vi_xml

TEST_VI = Path("samples/JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi")
SEARCH_PATHS = [Path("samples/OpenG/extracted")]


@pytest.fixture(scope="module")
def extracted_xml():
    """Extract VI XML once for all tests."""
    return extract_vi_xml(TEST_VI)


@pytest.fixture(scope="module")
def parsed_bd(extracted_xml):
    bd_xml, fp_xml, main_xml = extracted_xml
    return parse_block_diagram(bd_xml, fp_xml, main_xml)


@pytest.fixture(scope="module")
def graph():
    g = InMemoryVIGraph()
    g.load_vi(TEST_VI, expand_subvis=True, search_paths=SEARCH_PATHS)
    return g


class TestBlockDiagram:
    def test_qualified_name(self, parsed_bd):
        assert parsed_bd.qualified_name == "GraphicalTestRunner.lvlib:Get Settings Path.vi"

    def test_subvi_qualified_names_not_empty(self, parsed_bd):
        assert len(parsed_bd.subvi_qualified_names) > 0

    def test_subvi_names_format(self, parsed_bd):
        # SubVI qualified names should be in the format "name.vi" or "library:name.vi"
        for name in parsed_bd.subvi_qualified_names:
            assert ".vi" in name

    def test_nodes_not_empty(self, parsed_bd):
        assert len(parsed_bd.nodes) > 0

    def test_wires_not_empty(self, parsed_bd):
        assert len(parsed_bd.wires) > 0

    def test_terminal_info_not_empty(self, parsed_bd):
        assert len(parsed_bd.terminal_info) > 0


class TestTypeMap:
    def test_type_map_not_empty(self, extracted_xml):
        _, _, main_xml = extracted_xml
        type_map = parse_type_map_rich(main_xml)
        assert len(type_map) > 0

    def test_has_path_type(self, extracted_xml):
        _, _, main_xml = extracted_xml
        type_map = parse_type_map_rich(main_xml)
        assert any(t.underlying_type == "Path" for t in type_map.values())


class TestMetadata:
    def test_qualified_name(self, extracted_xml):
        _, _, main_xml = extracted_xml
        metadata = parse_vi_metadata(main_xml)
        assert metadata.get("qualified_name") == "GraphicalTestRunner.lvlib:Get Settings Path.vi"


class TestSubVIPaths:
    def test_refs_not_empty(self, extracted_xml):
        _, _, main_xml = extracted_xml
        refs = parse_subvi_paths(main_xml)
        assert len(refs) > 0

    def test_refs_have_qualified_names(self, extracted_xml):
        _, _, main_xml = extracted_xml
        refs = parse_subvi_paths(main_xml)
        for ref in refs:
            assert ref.qualified_name is not None


class TestMemoryGraph:
    def test_vi_loaded(self, graph):
        vi_name = "GraphicalTestRunner.lvlib:Get Settings Path.vi"
        assert vi_name in graph._loaded_vis

    def test_multiple_vis_loaded(self, graph):
        assert len(graph._loaded_vis) > 1

    def test_dependencies_exist(self, graph):
        deps = graph.get_vi_dependencies("GraphicalTestRunner.lvlib:Get Settings Path.vi")
        assert len(deps) > 0

    def test_vi_context_has_operations(self, graph):
        ctx = graph.get_vi_context("GraphicalTestRunner.lvlib:Get Settings Path.vi")
        assert "operations" in ctx
        assert len(ctx["operations"]) > 0

    def test_vi_context_has_inputs(self, graph):
        ctx = graph.get_vi_context("GraphicalTestRunner.lvlib:Get Settings Path.vi")
        assert "inputs" in ctx

    def test_dataflow_not_empty(self, graph):
        df = graph.get_dataflow_graph("GraphicalTestRunner.lvlib:Get Settings Path.vi")
        assert df is not None
        assert df.number_of_nodes() > 0
