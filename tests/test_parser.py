"""Tests for the parser module."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvpy.parser import (
    ParsedBlockDiagram,
    ParsedConnectorPane,
    ParsedConnectorPaneSlot,
    ParsedConstant,
    ParsedFPTerminal,
    ParsedLoopStructure,
    ParsedNode,
    ParsedSubVIPathRef,
    ParsedTerminalInfo,
    ParsedWire,
    ParsedWiringRule,
    TunnelMapping,
    parse_connector_pane,
    parse_polymorphic_info,
    parse_subvi_paths,
    parse_vi,
    parse_vi_metadata,
)

# === Model Dataclass Tests ===


class TestNode:
    """Tests for the ParsedNode dataclass."""

    def test_node_creation_minimal(self):
        """Test creating a ParsedNode with minimal fields."""
        node = ParsedNode(uid="123", node_type="prim")
        assert node.uid == "123"
        assert node.node_type == "prim"
        assert node.name is None
        assert node.inputs == []
        assert node.outputs == []

    def test_node_creation_full(self):
        """Test creating a ParsedNode with all fields."""
        node = ParsedNode(
            uid="456",
            node_type="iUse",
            name="MySubVI.vi",
            inputs=["term1", "term2"],
            outputs=["term3"],
            input_types=["int", "float"],
            output_types=["path"],
        )
        assert node.uid == "456"
        assert node.node_type == "iUse"
        assert node.name == "MySubVI.vi"
        assert len(node.inputs) == 2
        assert len(node.outputs) == 1


class TestConstant:
    """Tests for the Constant dataclass."""

    def test_constant_creation_minimal(self):
        """Test creating a Constant with required fields."""
        const = ParsedConstant(uid="c1", type_desc="stdNum", value="3F800000")
        assert const.uid == "c1"
        assert const.type_desc == "stdNum"
        assert const.value == "3F800000"
        assert const.label is None

    def test_constant_creation_with_label(self):
        """Test creating a Constant with a label."""
        const = ParsedConstant(
            uid="c2", type_desc="stdString", value="48656C6C6F", label="greeting"
        )
        assert const.label == "greeting"


class TestWire:
    """Tests for the Wire dataclass."""

    def test_wire_creation(self):
        """Test creating a Wire."""
        wire = ParsedWire(uid="w1", from_term="t1", to_term="t2")
        assert wire.uid == "w1"
        assert wire.from_term == "t1"
        assert wire.to_term == "t2"


class TestFPTerminal:
    """Tests for the FPTerminal dataclass."""

    def test_fp_terminal_input(self):
        """Test creating an input (control) FP terminal."""
        fp = ParsedFPTerminal(
            uid="fp1", fp_dco_uid="dco1", name="Input Value", is_indicator=False
        )
        assert fp.uid == "fp1"
        assert fp.fp_dco_uid == "dco1"
        assert fp.name == "Input Value"
        assert fp.is_indicator is False

    def test_fp_terminal_output(self):
        """Test creating an output (indicator) FP terminal."""
        fp = ParsedFPTerminal(
            uid="fp2", fp_dco_uid="dco2", name="Output Value", is_indicator=True
        )
        assert fp.is_indicator is True


class TestTerminalInfo:
    """Tests for the ParsedTerminalInfo dataclass."""

    def test_terminal_info_input(self):
        """Test creating an input terminal info."""
        info = ParsedTerminalInfo(
            uid="t1",
            parent_uid="node1",
            index=0,
            is_output=False,
            name="x",
        )
        assert info.uid == "t1"
        assert info.parent_uid == "node1"
        assert info.index == 0
        assert info.is_output is False
        assert info.parsed_type is None
        assert info.name == "x"

    def test_terminal_info_output(self):
        """Test creating an output terminal info."""
        info = ParsedTerminalInfo(
            uid="t2",
            parent_uid="node1",
            index=1,
            is_output=True,
        )
        assert info.is_output is True


class TestWiringRule:
    """Tests for the ParsedWiringRule class."""

    def test_wiring_rule_values(self):
        """Test ParsedWiringRule constants."""
        assert ParsedWiringRule.INVALID == 0
        assert ParsedWiringRule.REQUIRED == 1
        assert ParsedWiringRule.RECOMMENDED == 2
        assert ParsedWiringRule.OPTIONAL == 3
        assert ParsedWiringRule.DYNAMIC_DISPATCH == 4


class TestTunnelMapping:
    """Tests for the TunnelMapping dataclass."""

    def test_tunnel_lsr_direction(self):
        """Test left shift register tunnel direction."""
        tunnel = TunnelMapping(
            outer_terminal_uid="outer1",
            inner_terminal_uid="inner1",
            tunnel_type="lSR",
        )
        assert tunnel.direction == "in"

    def test_tunnel_rsr_direction(self):
        """Test right shift register tunnel direction."""
        tunnel = TunnelMapping(
            outer_terminal_uid="outer2",
            inner_terminal_uid="inner2",
            tunnel_type="rSR",
        )
        assert tunnel.direction == "out"

    def test_tunnel_lmax_direction(self):
        """Test lMax tunnel direction."""
        tunnel = TunnelMapping(
            outer_terminal_uid="outer3",
            inner_terminal_uid="inner3",
            tunnel_type="lMax",
        )
        assert tunnel.direction == "out"

    def test_tunnel_lptun_direction(self):
        """Test loop tunnel direction is unknown."""
        tunnel = TunnelMapping(
            outer_terminal_uid="outer4",
            inner_terminal_uid="inner4",
            tunnel_type="lpTun",
        )
        assert tunnel.direction == "unknown"

    def test_tunnel_with_paired_terminal(self):
        """Test tunnel with paired terminal (shift register)."""
        tunnel = TunnelMapping(
            outer_terminal_uid="outer5",
            inner_terminal_uid="inner5",
            tunnel_type="lSR",
            paired_terminal_uid="paired5",
        )
        assert tunnel.paired_terminal_uid == "paired5"


class TestLoopStructure:
    """Tests for the ParsedLoopStructure dataclass."""

    def test_while_loop_creation(self):
        """Test creating a while loop structure."""
        loop = ParsedLoopStructure(
            uid="loop1",
            loop_type="whileLoop",
            boundary_terminal_uids=["bt1", "bt2"],
            inner_diagram_uid="inner1",
            inner_node_uids=["n1", "n2"],
            stop_condition_terminal_uid="stop1",
        )
        assert loop.uid == "loop1"
        assert loop.loop_type == "whileLoop"
        assert len(loop.boundary_terminal_uids) == 2
        assert loop.stop_condition_terminal_uid == "stop1"

    def test_for_loop_creation(self):
        """Test creating a for loop structure."""
        loop = ParsedLoopStructure(
            uid="loop2",
            loop_type="forLoop",
            tunnels=[
                TunnelMapping(
                    outer_terminal_uid="o1", inner_terminal_uid="i1",
                    tunnel_type="lpTun",
                ),
                TunnelMapping(
                    outer_terminal_uid="o2", inner_terminal_uid="i2",
                    tunnel_type="lMax",
                ),
            ],
        )
        assert loop.loop_type == "forLoop"
        assert len(loop.tunnels) == 2


class TestConnectorPaneSlot:
    """Tests for the ParsedConnectorPaneSlot dataclass."""

    def test_slot_empty(self):
        """Test creating an empty slot."""
        slot = ParsedConnectorPaneSlot(index=0)
        assert slot.index == 0
        assert slot.fp_dco_uid is None
        assert slot.is_output is False
        assert slot.wiring_rule == 0

    def test_slot_connected(self):
        """Test creating a connected slot."""
        slot = ParsedConnectorPaneSlot(
            index=3,
            fp_dco_uid="dco123",
            is_output=True,
            wiring_rule=ParsedWiringRule.REQUIRED,
            type_id="TypeID(10)",
        )
        assert slot.fp_dco_uid == "dco123"
        assert slot.is_output is True
        assert slot.wiring_rule == 1


class TestConnectorPane:
    """Tests for the ParsedConnectorPane dataclass."""

    def test_connector_pane_creation(self):
        """Test creating a connector pane."""
        pane = ParsedConnectorPane(
            pattern_id=4,
            slots=[
                ParsedConnectorPaneSlot(index=0, fp_dco_uid="dco1"),
                ParsedConnectorPaneSlot(index=1),
                ParsedConnectorPaneSlot(index=2, fp_dco_uid="dco2"),
            ],
        )
        assert pane.pattern_id == 4
        assert len(pane.slots) == 3

    def test_get_connected_uids(self):
        """Test getting connected UIDs from connector pane."""
        pane = ParsedConnectorPane(
            pattern_id=4,
            slots=[
                ParsedConnectorPaneSlot(index=0, fp_dco_uid="dco1"),
                ParsedConnectorPaneSlot(index=1),  # Empty slot
                ParsedConnectorPaneSlot(index=2, fp_dco_uid="dco2"),
            ],
        )
        connected = pane.get_connected_uids()
        assert len(connected) == 2
        assert "dco1" in connected
        assert "dco2" in connected


class TestSubVIPathRef:
    """Tests for the ParsedSubVIPathRef dataclass."""

    def test_vilib_path_ref(self):
        """Test a vi.lib path reference."""
        ref = ParsedSubVIPathRef(
            name="File Exists.vi",
            path_tokens=["<vilib>", "Utility", "file.llb", "File Exists.vi"],
            is_vilib=True,
        )
        assert ref.name == "File Exists.vi"
        assert ref.is_vilib is True
        assert ref.get_relative_path() == "Utility/file.llb/File Exists.vi"

    def test_userlib_path_ref(self):
        """Test a user.lib path reference."""
        ref = ParsedSubVIPathRef(
            name="MyHelper.vi",
            path_tokens=["<userlib>", "MyLib", "MyHelper.vi"],
            is_userlib=True,
        )
        assert ref.is_userlib is True
        assert ref.get_relative_path() == "MyLib/MyHelper.vi"

    def test_local_path_ref(self):
        """Test a local path reference."""
        ref = ParsedSubVIPathRef(
            name="Local.vi",
            path_tokens=["SubFolder", "Local.vi"],
        )
        assert ref.is_vilib is False
        assert ref.is_userlib is False
        assert ref.get_relative_path() == "SubFolder/Local.vi"


class TestBlockDiagram:
    """Tests for the ParsedBlockDiagram dataclass."""

    def test_block_diagram_creation(self):
        """Test creating a ParsedBlockDiagram."""
        bd = ParsedBlockDiagram(
            nodes=[ParsedNode(uid="n1", node_type="prim")],
            constants=[ParsedConstant(uid="c1", type_desc="stdNum", value="0")],
            wires=[ParsedWire(uid="w1", from_term="t1", to_term="t2")],
        )
        assert len(bd.nodes) == 1
        assert len(bd.constants) == 1
        assert len(bd.wires) == 1
        assert bd.loops == []

    def test_get_node(self):
        """Test getting a node by UID."""
        node1 = ParsedNode(uid="n1", node_type="prim", name="Add")
        node2 = ParsedNode(uid="n2", node_type="iUse", name="SubVI.vi")
        bd = ParsedBlockDiagram(nodes=[node1, node2], constants=[], wires=[])

        found = bd.get_node("n1")
        assert found is not None
        assert found.name == "Add"

        not_found = bd.get_node("n99")
        assert not_found is None

    def test_get_parent_uid(self):
        """Test getting parent UID for a terminal."""
        bd = ParsedBlockDiagram(
            nodes=[],
            constants=[],
            wires=[],
            terminal_info={
                "t1": ParsedTerminalInfo(
                    uid="t1", parent_uid="node1", index=0, is_output=False
                ),
            },
        )
        assert bd.get_parent_uid("t1") == "node1"
        assert bd.get_parent_uid("t99") is None

    def test_get_loop(self):
        """Test getting a loop by UID."""
        loop = ParsedLoopStructure(uid="loop1", loop_type="whileLoop")
        bd = ParsedBlockDiagram(nodes=[], constants=[], wires=[], loops=[loop])

        found = bd.get_loop("loop1")
        assert found is not None
        assert found.loop_type == "whileLoop"

        not_found = bd.get_loop("loop99")
        assert not_found is None

    def test_get_tunnel_mapping(self):
        """Test getting tunnel mapping for a terminal."""
        tunnel = TunnelMapping(
            outer_terminal_uid="outer1",
            inner_terminal_uid="inner1",
            tunnel_type="lpTun",
        )
        loop = ParsedLoopStructure(uid="loop1", loop_type="forLoop", tunnels=[tunnel])
        bd = ParsedBlockDiagram(nodes=[], constants=[], wires=[], loops=[loop])

        # Find by outer terminal
        found_outer = bd.get_tunnel_mapping("outer1")
        assert found_outer is not None
        assert found_outer.tunnel_type == "lpTun"

        # Find by inner terminal
        found_inner = bd.get_tunnel_mapping("inner1")
        assert found_inner is not None
        assert found_inner.tunnel_type == "lpTun"

        # Not found
        not_found = bd.get_tunnel_mapping("other")
        assert not_found is None


# === XML Parsing Tests ===


class TestParseVI:
    """Tests for parse_vi function."""

    def test_parse_minimal_block_diagram(self, tmp_path: Path):
        """Test parsing a minimal block diagram XML."""
        xml_content = """<?xml version="1.0"?>
<root>
    <signalList></signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert bd is not None
        assert len(bd.nodes) == 0
        assert len(bd.constants) == 0
        assert len(bd.wires) == 0

    def test_parse_with_primitive(self, tmp_path: Path):
        """Test parsing a block diagram with a primitive node."""
        xml_content = """<?xml version="1.0"?>
<root>
    <node class="prim" uid="prim1">
        <primIndex>10</primIndex>
        <primResID>1419</primResID>
        <termList></termList>
    </node>
    <signalList></signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.nodes) == 1
        from lvpy.parser.node_types import PrimitiveNode
        node = bd.nodes[0]
        assert node.uid == "prim1"
        assert node.node_type == "prim"
        assert isinstance(node, PrimitiveNode)
        assert node.prim_index == 10
        assert node.prim_res_id == 1419

    def test_parse_with_subvi(self, tmp_path: Path):
        """Test parsing a block diagram with a SubVI node."""
        xml_content = """<?xml version="1.0"?>
<root>
    <node class="iUse" uid="subvi1">
        <label><textRec><text>"My Helper.vi"</text></textRec></label>
        <termList></termList>
    </node>
    <signalList></signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.nodes) == 1
        node = bd.nodes[0]
        assert node.uid == "subvi1"
        assert node.node_type == "iUse"
        assert node.name == "My Helper.vi"

    def test_parse_with_wires(self, tmp_path: Path):
        """Test parsing a block diagram with wires."""
        xml_content = """<?xml version="1.0"?>
<root>
    <signalList>
        <SL__arrayElement class="signal" uid="sig1">
            <termList>
                <SL__arrayElement uid="t1"/>
                <SL__arrayElement uid="t2"/>
            </termList>
        </SL__arrayElement>
    </signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.wires) == 1
        wire = bd.wires[0]
        assert wire.from_term == "t1"
        assert wire.to_term == "t2"

    def test_parse_with_multiway_wire(self, tmp_path: Path):
        """Test parsing a wire with multiple destinations."""
        xml_content = """<?xml version="1.0"?>
<root>
    <signalList>
        <SL__arrayElement class="signal" uid="sig1">
            <termList>
                <SL__arrayElement uid="source"/>
                <SL__arrayElement uid="dest1"/>
                <SL__arrayElement uid="dest2"/>
            </termList>
        </SL__arrayElement>
    </signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.wires) == 2
        assert bd.wires[0].from_term == "source"
        assert bd.wires[0].to_term == "dest1"
        assert bd.wires[1].from_term == "source"
        assert bd.wires[1].to_term == "dest2"

    def test_parse_with_constant(self, tmp_path: Path):
        """Test parsing a block diagram with a constant."""
        # Constants are found as terminals with dco[@class='bDConstDCO']
        xml_content = """<?xml version="1.0"?>
<root>
    <nodeList>
        <SL__arrayElement class="term" uid="const1">
            <dco class="bDConstDCO">
                <typeDesc>stdNum</typeDesc>
                <ConstValue>3F800000</ConstValue>
            </dco>
        </SL__arrayElement>
    </nodeList>
    <signalList></signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.constants) == 1
        const = bd.constants[0]
        assert const.uid == "const1"
        assert const.type_desc == "stdNum"
        assert const.value == "3F800000"

    def test_parse_with_fp_terminals(self, tmp_path: Path):
        """Test parsing a block diagram with front panel terminals."""
        xml_content = """<?xml version="1.0"?>
<root>
    <node class="fPTerm" uid="fp1">
        <dco uid="dco1"/>
        <label><textRec><text>"Input"</text></textRec></label>
    </node>
    <node class="fPTerm" uid="fp2">
        <dco uid="dco2"/>
        <label><textRec><text>"Output"</text></textRec></label>
    </node>
    <signalList>
        <SL__arrayElement class="signal" uid="sig1">
            <termList>
                <SL__arrayElement uid="t1"/>
                <SL__arrayElement uid="fp2"/>
            </termList>
        </SL__arrayElement>
    </signalList>
</root>"""
        xml_file = tmp_path / "test_BDHb.xml"
        xml_file.write_text(xml_content)

        vi = parse_vi(bd_xml=xml_file)
        bd = vi.block_diagram
        assert len(bd.fp_terminals) == 2

        # fp1 has no incoming wire - it's an input
        fp1 = next(fp for fp in bd.fp_terminals if fp.uid == "fp1")
        assert fp1.is_indicator is False

        # fp2 has an incoming wire - it's an output
        fp2 = next(fp for fp in bd.fp_terminals if fp.uid == "fp2")
        assert fp2.is_indicator is True


class TestParseConnectorPane:
    """Tests for parse_connector_pane function."""

    def test_parse_connector_pane(self, tmp_path: Path):
        """Test parsing a connector pane from FP XML."""
        xml_content = """<?xml version="1.0"?>
<root>
    <conPane class="conPane">
        <conId>4</conId>
        <cons>
            <SL__arrayElement class="ConpaneConnection" index="0">
                <ConnectionDCO uid="dco1"/>
            </SL__arrayElement>
            <SL__arrayElement class="ConpaneConnection" index="2">
                <ConnectionDCO uid="dco2"/>
            </SL__arrayElement>
        </cons>
    </conPane>
</root>"""
        xml_file = tmp_path / "test_FPHb.xml"
        xml_file.write_text(xml_content)

        pane = parse_connector_pane(xml_file)
        assert pane is not None
        assert pane.pattern_id == 4
        assert len(pane.slots) == 2
        assert pane.slots[0].index == 0
        assert pane.slots[0].fp_dco_uid == "dco1"
        assert pane.slots[1].index == 2
        assert pane.slots[1].fp_dco_uid == "dco2"

    def test_parse_connector_pane_missing(self, tmp_path: Path):
        """Test parsing when no connector pane exists."""
        xml_content = """<?xml version="1.0"?>
<root></root>"""
        xml_file = tmp_path / "test_FPHb.xml"
        xml_file.write_text(xml_content)

        pane = parse_connector_pane(xml_file)
        assert pane is None


class TestParseSubviPaths:
    """Tests for parse_subvi_paths function."""

    def test_parse_vilib_subvi(self, tmp_path: Path):
        """Test parsing a vi.lib SubVI reference."""
        xml_content = """<?xml version="1.0"?>
<root>
    <LIvi>
        <Section>
            <VIVI>
                <LinkSaveQualName><String>File Exists.vi</String></LinkSaveQualName>
                <LinkSavePathRef>
                    <String>&lt;vilib&gt;</String>
                    <String>Utility</String>
                    <String>file.llb</String>
                    <String>File Exists.vi</String>
                </LinkSavePathRef>
            </VIVI>
        </Section>
    </LIvi>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        refs = parse_subvi_paths(xml_file)
        assert len(refs) == 1
        ref = refs[0]
        assert ref.name == "File Exists.vi"
        assert ref.is_vilib is True
        assert ref.is_userlib is False

    def test_parse_userlib_subvi(self, tmp_path: Path):
        """Test parsing a user.lib SubVI reference."""
        xml_content = """<?xml version="1.0"?>
<root>
    <LIvi>
        <Section>
            <VIVI>
                <LinkSaveQualName><String>MyHelper__ogtk.vi</String></LinkSaveQualName>
                <LinkSavePathRef>
                    <String>&lt;userlib&gt;</String>
                    <String>_OpenG.lib</String>
                    <String>MyHelper__ogtk.vi</String>
                </LinkSavePathRef>
            </VIVI>
        </Section>
    </LIvi>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        refs = parse_subvi_paths(xml_file)
        assert len(refs) == 1
        ref = refs[0]
        assert ref.name == "MyHelper__ogtk.vi"
        assert ref.is_vilib is False
        assert ref.is_userlib is True


class TestParseViMetadata:
    """Tests for parse_vi_metadata function."""

    def test_parse_basic_metadata(self, tmp_path: Path):
        """Test parsing basic VI metadata."""
        xml_content = """<?xml version="1.0"?>
<root>
    <LVSR><Section Name="My Test VI.vi"/></LVSR>
    <LIBN><Section><Library>TestLib</Library></Section></LIBN>
    <LIvi><Section><LVIN Unk1="TestLib:My Test VI.vi"/></Section></LIvi>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        metadata = parse_vi_metadata(xml_file)
        assert metadata["name"] == "My Test VI.vi"
        assert metadata["library"] == "TestLib"
        assert metadata["qualified_name"] == "TestLib:My Test VI.vi"

    def test_parse_with_description(self, tmp_path: Path):
        """Test parsing VI with description."""
        xml_content = """<?xml version="1.0"?>
<root>
    <LVSR><Section Name="Test.vi"/></LVSR>
    <DSTM><Section><String>This is a test VI</String></Section></DSTM>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        metadata = parse_vi_metadata(xml_file)
        assert metadata["description"] == "This is a test VI"


class TestParsePolymorphicInfo:
    """Tests for parse_polymorphic_info function."""

    def test_non_polymorphic_vi(self, tmp_path: Path):
        """Test parsing a non-polymorphic VI."""
        xml_content = """<?xml version="1.0"?>
<root>
    <VCTP><Section><TypeDesc Type="Function"/></Section></VCTP>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_file)
        root = tree.getroot()

        info = parse_polymorphic_info(root)
        assert info["is_polymorphic"] is False
        assert info["variants"] == []
        assert info["selectors"] == []

    def test_polymorphic_vi(self, tmp_path: Path):
        """Test parsing a polymorphic VI."""
        xml_content = """<?xml version="1.0"?>
<root>
    <LVSR><Section><Execution2 AllowPolyTypeAdapt="1"/></Section></LVSR>
    <VCTP><Section><TypeDesc Type="PolyVI"/></Section></VCTP>
    <CPST><Section>
        <String>I8</String>
        <String>I16</String>
        <String>I32</String>
    </Section></CPST>
    <LIvi><Section>
        <VIVI><LinkSaveQualName><String>Add I8.vi</String></LinkSaveQualName></VIVI>
        <VIVI><LinkSaveQualName><String>Add I16.vi</String></LinkSaveQualName></VIVI>
        <VIVI><LinkSaveQualName><String>Add I32.vi</String></LinkSaveQualName></VIVI>
    </Section></LIvi>
</root>"""
        xml_file = tmp_path / "test.xml"
        xml_file.write_text(xml_content)

        import xml.etree.ElementTree as ET
        tree = ET.parse(xml_file)
        root = tree.getroot()

        info = parse_polymorphic_info(root)
        assert info["is_polymorphic"] is True
        assert len(info["selectors"]) == 3
        assert "I8" in info["selectors"]
        assert len(info["variants"]) == 3
        assert "Add I8.vi" in info["variants"]


# === Integration Tests with Real VIs ===


class TestRealVIParsing:
    """Integration tests using real VI files from samples."""

    @pytest.fixture
    def sample_vi_path(self) -> Path | None:
        """Get path to a sample VI if available."""
        path = Path(
            "samples/JKI-VI-Tester/source/User Interfaces/"
            "Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi"
        )
        if path.exists():
            return path
        return None

    def test_parse_real_vi(self, sample_vi_path: Path | None):
        """Test parsing a real VI file."""
        if sample_vi_path is None:
            pytest.skip("Sample VI not available")

        from lvpy.extractor import extract_vi_xml

        bd_xml, fp_xml, main_xml = extract_vi_xml(sample_vi_path)

        # Parse VI
        vi = parse_vi(bd_xml=bd_xml)
        bd = vi.block_diagram
        assert bd is not None
        assert len(bd.nodes) > 0 or len(bd.constants) > 0 or len(bd.wires) > 0

        # Parse connector pane if available
        if fp_xml and fp_xml.exists():
            pane = parse_connector_pane(fp_xml)
            # May or may not have a connector pane
            if pane is not None:
                assert pane.pattern_id >= 0

        # Parse metadata if available
        if main_xml and main_xml.exists():
            metadata = parse_vi_metadata(main_xml)
            assert "name" in metadata or "qualified_name" in metadata
