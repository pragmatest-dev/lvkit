"""Regression tests for parser refactoring."""

from pathlib import Path

import pytest

from lvkit.extractor import extract_vi_xml
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.parser import ParsedVI, ParsedVIMetadata, parse_vi
from lvkit.parser.metadata import parse_subvi_paths, parse_vi_metadata
from lvkit.parser.type_mapping import parse_type_map_rich

# Regression suite over the local-only sample corpus.
pytestmark = pytest.mark.needs_samples

TEST_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
    "Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi"
)
SEARCH_PATHS = [Path(".lvkit/cache/samples/OpenG/extracted")]


@pytest.fixture(scope="module")
def extracted_xml():
    """Extract VI XML once for all tests."""
    return extract_vi_xml(TEST_VI)


@pytest.fixture(scope="module")
def parsed_vi(extracted_xml) -> ParsedVI:
    """Parse VI once for all tests."""
    # Pass vi_path so parse_vi can record the original source location
    # (bd_xml may now be in a temp cache dir rather than next to the VI).
    return parse_vi(vi_path=TEST_VI)


@pytest.fixture(scope="module")
def parsed_bd(parsed_vi):
    """Get block diagram from parsed VI."""
    return parsed_vi.block_diagram


@pytest.fixture(scope="module")
def parsed_metadata(parsed_vi) -> ParsedVIMetadata:
    """Get metadata from parsed VI."""
    return parsed_vi.metadata


@pytest.fixture(scope="module")
def graph():
    g = InMemoryVIGraph()
    g.load_vi(TEST_VI, mode=LoadMode.FULL, search_paths=SEARCH_PATHS)
    return g


class TestVIMetadata:
    """Tests for VIMetadata (qualified_name, subvi refs, source_path)."""

    def test_qualified_name(self, parsed_metadata):
        expected = "GraphicalTestRunner.lvlib:Get Settings Path.vi"
        assert parsed_metadata.qualified_name == expected

    def test_subvi_qualified_names_not_empty(self, parsed_metadata):
        assert len(parsed_metadata.subvi_qualified_names) > 0

    def test_subvi_names_format(self, parsed_metadata):
        # SubVI qualified names should be in the format "name.vi" or "library:name.vi"
        for name in parsed_metadata.subvi_qualified_names:
            assert ".vi" in name

    def test_source_path_exists(self, parsed_metadata):
        assert parsed_metadata.source_path is not None
        assert parsed_metadata.source_path.endswith(".vi")


class TestBlockDiagram:
    """Tests for BlockDiagram content (nodes, wires, terminals)."""

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
        expected = "GraphicalTestRunner.lvlib:Get Settings Path.vi"
        assert metadata.get("qualified_name") == expected


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
        # VIs are keyed by path (identity) now; the qname resolves to that key.
        assert graph.resolve_vi_name(vi_name) in graph._loaded_vis

    def test_multiple_vis_loaded(self, graph):
        assert len(graph._loaded_vis) > 1

    def test_dependencies_exist(self, graph):
        vi_name = "GraphicalTestRunner.lvlib:Get Settings Path.vi"
        deps = graph.get_vi_dependencies(vi_name)
        assert len(deps) > 0

    def test_vi_context_has_operations(self, graph):
        ctx = graph.get_vi_context("GraphicalTestRunner.lvlib:Get Settings Path.vi")
        assert len(ctx.operations) > 0

    def test_vi_context_has_inputs(self, graph):
        ctx = graph.get_vi_context("GraphicalTestRunner.lvlib:Get Settings Path.vi")
        assert ctx.inputs is not None

    def test_dataflow_not_empty(self, graph):
        df = graph.get_dataflow_graph("GraphicalTestRunner.lvlib:Get Settings Path.vi")
        assert df is not None
        assert df.number_of_nodes() > 0


class TestWhileLoopNestedShiftRegister:
    """A while-loop serializes its RIGHT shift register NESTED inside the LEFT
    one (``<rsrDCO class="rSR">``), unlike a for-loop where the rSR is its own
    term. The loop parser must extract the nested rSR so the pair is complete —
    otherwise the right register's terminal, border glyph, wire type, AND
    generated-code dataflow all silently vanish (task #96 follow-up)."""

    # Point at the .vi SOURCE, not a sibling _BDHb.xml: extracted XML now lives
    # in the project-local cache, so resolve it through the extractor (which
    # extracts on a cache miss) rather than hardcoding either location.
    LIST_VI = (
        ".lvkit/cache/samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/"
        "appcontrol/appcontrol.llb/List VI Hierarchy__ogtk.vi"
    )

    @pytest.fixture(scope="class")
    def while_loop(self):
        import xml.etree.ElementTree as ET

        from lvkit.extractor import resolve_extracted
        from lvkit.parser.nodes.loop import extract_loops

        vi = Path(self.LIST_VI)
        if not vi.exists():
            pytest.skip("List VI Hierarchy sample not present")
        bd, _fp, _main = resolve_extracted(vi)
        root = ET.parse(bd).getroot()
        inner = root.find("root")
        if inner is not None:
            root = inner
        whiles = [lp for lp in extract_loops(root) if lp.loop_type == "whileLoop"]
        assert whiles, "expected a while loop"
        return whiles[0]

    def test_both_sr_sides_extracted_and_paired(self, while_loop):
        lsr = [t for t in while_loop.tunnels if t.tunnel_type == "lSR"]
        rsr = [t for t in while_loop.tunnels if t.tunnel_type == "rSR"]
        # This VI's while loop has three shift-register pairs.
        assert len(lsr) == 3
        assert len(rsr) == 3
        # Every register is paired to its mate (was left None pre-fix, when the
        # nested rSR was never extracted so there was nothing to pair).
        assert all(t.paired_terminal_uid for t in lsr)
        assert all(t.paired_terminal_uid for t in rsr)


class TestResilientVCTPExport:
    """A single misaligned/corrupt flat type (e.g. an ``LVVariant`` whose garbage
    4-byte count trips ``typedesc_list_limit``, or expands to a multi-MB junk
    DataFill) used to abort VCTP XML export at the SECTION level, dumping the
    ENTIRE Consolidated Type Pool to raw ``.bin`` — so the main XML lost every
    ``FlatTypeID`` TypeDesc and the ``<TopLevel>`` map, and no cluster/typedef
    terminal could resolve its field names (Bundle/Unbundle By Name went blank /
    [index]-only). Patch #6 (``_pylabview_patches``) makes the per-type export
    resilient: the corrupt type becomes a position-preserving stub and the rest
    of the pool + ``<TopLevel>`` still serialize, so field names resolve again.

    Uses ``GrabWebCam_PCO_IOS.vi`` (a small IMAQ-camera VI in the affected
    family — ~110 KB front panel) rather than ``MasterAcquisitionFile_PCO_IOS``
    (whose front-panel heap balloons to ~150 MB from the same corrupt variant, a
    separate blowup) so the test stays cheap."""

    @pytest.fixture(scope="class")
    def main_xml(self):
        ms = list(Path(".lvkit/cache/samples").rglob("GrabWebCam_PCO_IOS.vi"))
        if not ms:
            pytest.skip("GrabWebCam_PCO_IOS sample not present")
        _bd, _fp, main = extract_vi_xml(ms[0], force=True)
        assert main is not None
        return Path(main)

    def test_vctp_recovered_not_bin_fallback(self, main_xml):
        text = main_xml.read_text(errors="replace")
        # The corrupt type is stubbed (patch fired) ...
        assert "corrupt-stub" in text
        # ... yet the rest of the pool + the consolidated->flat map serialized,
        # instead of the whole section collapsing to Format="bin".
        assert "FlatTypeID" in text
        assert "<TopLevel>" in text

    def test_typedef_cluster_field_names_resolve(self, main_xml):
        type_map = parse_type_map_rich(main_xml)
        named = [
            [f.name for f in lt.fields]
            for lt in type_map.values()
            if lt.kind == "cluster" and lt.fields
        ]
        # With the pool recovered, typedef/anonymous clusters carry their field
        # names again (was zero when the whole VCTP fell back to bin).
        assert named, "no cluster resolved its field names"
        # The standard error cluster is present and correctly named.
        assert ["status", "code", "source"] in named

    def test_patch_is_a_wrapper_over_the_original(self):
        # Patch #6 defers to the original export for every valid type (byte
        # -identical); only a type that raises falls into the stub path.
        import pylabview.LVblock as lv_block

        from lvkit._pylabview_patches import install_pylabview_patches

        install_pylabview_patches()
        assert hasattr(lv_block.VCTP.exportXMLTypeDescList, "__wrapped__")
