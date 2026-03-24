"""Tests for sequence structure support (flat and stacked)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from vipy.agent.codegen.context import CodeGenContext
from vipy.agent.codegen.nodes.sequence import FlatSequenceCodeGen
from vipy.graph_types import (
    CaseFrame,
    FrameInfo,
    Operation,
    StructureNode,
    Terminal,
    Tunnel,
    TunnelTerminal,
    VINode,
    Wire,
    WireEnd,
)
from vipy.memory_graph import InMemoryVIGraph
from vipy.parser.models import (
    BlockDiagram,
    FlatSequenceStructure,
    SequenceFrame,
)
from vipy.parser.nodes.sequence import extract_flat_sequences

# === Parser Tests ===


class TestSequenceFrameModel:
    """Tests for the SequenceFrame and FlatSequenceStructure dataclasses."""

    def test_sequence_frame_creation(self):
        frame = SequenceFrame(uid="f1", inner_node_uids=["n1", "n2"])
        assert frame.uid == "f1"
        assert frame.inner_node_uids == ["n1", "n2"]

    def test_sequence_frame_defaults(self):
        frame = SequenceFrame(uid="f1")
        assert frame.inner_node_uids == []

    def test_flat_sequence_structure(self):
        t = Tunnel(
            outer_terminal_uid="o1",
            inner_terminal_uid="i1",
            tunnel_type="seqTun",
        )
        fs = FlatSequenceStructure(
            uid="seq1",
            tunnels=[t],
            frames=[
                SequenceFrame(uid="f0", inner_node_uids=["a"]),
                SequenceFrame(uid="f1", inner_node_uids=["b"]),
            ],
        )
        assert fs.uid == "seq1"
        assert len(fs.tunnels) == 1
        assert len(fs.frames) == 2

    def test_block_diagram_flat_sequences_field(self):
        bd = BlockDiagram(nodes=[], constants=[], wires=[])
        assert bd.flat_sequences == []

        fs = FlatSequenceStructure(uid="seq1")
        bd.flat_sequences.append(fs)
        assert len(bd.flat_sequences) == 1

    def test_block_diagram_get_tunnel_mapping_checks_sequences(self):
        t = Tunnel(
            outer_terminal_uid="outer1",
            inner_terminal_uid="inner1",
            tunnel_type="seqTun",
        )
        fs = FlatSequenceStructure(uid="seq1", tunnels=[t])
        bd = BlockDiagram(
            nodes=[], constants=[], wires=[],
            flat_sequences=[fs],
        )
        found = bd.get_tunnel_mapping("outer1")
        assert found is not None
        assert found.tunnel_type == "seqTun"

        found_inner = bd.get_tunnel_mapping("inner1")
        assert found_inner is not None

        assert bd.get_tunnel_mapping("nonexistent") is None


class TestExtractFlatSequences:
    """Tests for extract_flat_sequences() XML parsing."""

    def _make_xml(self, xml_str: str) -> ET.Element:
        return ET.fromstring(
            f'<?xml version="1.0"?><root>{xml_str}</root>'
        )

    def test_empty_document(self):
        root = self._make_xml("")
        result = extract_flat_sequences(root)
        assert result == []

    def test_flat_sequence_basic(self):
        xml = """
        <SL__arrayElement class="flatSequence" uid="100">
          <sequenceList elements="2">
            <SL__arrayElement class="sequenceFrame" uid="200">
              <diagramList elements="1">
                <SL__arrayElement class="diag" uid="201">
                  <nodeList elements="1">
                    <SL__arrayElement uid="300" />
                  </nodeList>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
            <SL__arrayElement class="sequenceFrame" uid="400">
              <diagramList elements="1">
                <SL__arrayElement class="diag" uid="401">
                  <nodeList elements="1">
                    <SL__arrayElement uid="500" />
                  </nodeList>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
          </sequenceList>
        </SL__arrayElement>
        """
        root = self._make_xml(xml)
        result = extract_flat_sequences(root)

        assert len(result) == 1
        fs = result[0]
        assert fs.uid == "100"
        assert len(fs.frames) == 2
        assert fs.frames[0].uid == "200"
        assert fs.frames[0].inner_node_uids == ["300"]
        assert fs.frames[1].uid == "400"
        assert fs.frames[1].inner_node_uids == ["500"]

    def test_flat_sequence_with_tunnels(self):
        xml = """
        <SL__arrayElement class="flatSequence" uid="100">
          <sequenceList elements="1">
            <SL__arrayElement class="sequenceFrame" uid="200">
              <termList elements="1">
                <SL__arrayElement class="term" uid="210">
                  <dco class="seqTun" uid="211">
                    <termList elements="2">
                      <SL__arrayElement uid="220" />
                      <SL__arrayElement uid="210" />
                    </termList>
                  </dco>
                </SL__arrayElement>
              </termList>
              <diagramList elements="1">
                <SL__arrayElement class="diag" uid="201">
                  <nodeList elements="0" />
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
          </sequenceList>
        </SL__arrayElement>
        """
        root = self._make_xml(xml)
        result = extract_flat_sequences(root)

        assert len(result) == 1
        fs = result[0]
        assert len(fs.tunnels) == 1
        tunnel = fs.tunnels[0]
        assert tunnel.tunnel_type == "seqTun"
        assert tunnel.outer_terminal_uid == "210"
        assert tunnel.inner_terminal_uid == "220"

    def test_stacked_sequence_basic(self):
        xml = """
        <SL__arrayElement class="seq" uid="100">
          <diagramList elements="2">
            <SL__arrayElement class="diag" uid="200">
              <nodeList elements="1">
                <SL__arrayElement uid="300" />
              </nodeList>
            </SL__arrayElement>
            <SL__arrayElement class="diag" uid="400">
              <nodeList elements="1">
                <SL__arrayElement uid="500" />
              </nodeList>
            </SL__arrayElement>
          </diagramList>
        </SL__arrayElement>
        """
        root = self._make_xml(xml)
        result = extract_flat_sequences(root)

        assert len(result) == 1
        fs = result[0]
        assert fs.uid == "100"
        assert len(fs.frames) == 2
        assert fs.frames[0].inner_node_uids == ["300"]
        assert fs.frames[1].inner_node_uids == ["500"]

    def test_multiple_inner_nodes(self):
        xml = """
        <SL__arrayElement class="flatSequence" uid="100">
          <sequenceList elements="1">
            <SL__arrayElement class="sequenceFrame" uid="200">
              <diagramList elements="1">
                <SL__arrayElement class="diag" uid="201">
                  <nodeList elements="3">
                    <SL__arrayElement uid="301" />
                    <SL__arrayElement uid="302" />
                    <SL__arrayElement uid="303" />
                  </nodeList>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
          </sequenceList>
        </SL__arrayElement>
        """
        root = self._make_xml(xml)
        result = extract_flat_sequences(root)

        assert len(result) == 1
        assert result[0].frames[0].inner_node_uids == [
            "301", "302", "303",
        ]

    def test_no_uid_skipped(self):
        xml = """
        <SL__arrayElement class="flatSequence">
          <sequenceList elements="1">
            <SL__arrayElement class="sequenceFrame" uid="200">
              <diagramList elements="1">
                <SL__arrayElement class="diag" uid="201">
                  <nodeList elements="0" />
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
          </sequenceList>
        </SL__arrayElement>
        """
        root = self._make_xml(xml)
        result = extract_flat_sequences(root)
        assert result == []


# === Memory Graph Tests ===


class TestSequenceInMemoryGraph:
    """Tests for flat sequence handling in InMemoryVIGraph."""

    @pytest.fixture
    def graph_with_sequence(self) -> InMemoryVIGraph:
        """Create a graph with a flat sequence structure.

        Uses typed graph nodes (StructureNode, VINode) stored as
        graph.nodes[uid]["node"] on the unified MultiDiGraph.
        Structure data (tunnels, inner_node_uids, frames) lives
        ON the StructureNode.
        """
        vi_name = "Seq.vi"
        graph = InMemoryVIGraph()
        g = graph._graph

        # VINode for the VI itself (FP terminals)
        vi_node = VINode(
            id=vi_name, vi=vi_name, name=vi_name,
            terminals=[],
        )
        g.add_node(vi_name, node=vi_node)

        # Flat sequence structure node — tunnels, frames, inner_node_uids
        # all stored ON the StructureNode
        seq_node = StructureNode(
            id="seq1", vi=vi_name,
            name="Flat Sequence",
            node_type="flatSequence",
            frames=[
                FrameInfo(selector_value="0"),
                FrameInfo(selector_value="1"),
            ],
            terminals=[
                TunnelTerminal(
                    id="tun_outer", index=0, direction="input",
                    tunnel_type="seqTun", boundary="outer",
                    paired_id="tun_inner",
                ),
                TunnelTerminal(
                    id="tun_inner", index=0, direction="input",
                    tunnel_type="seqTun", boundary="inner",
                    paired_id="tun_outer",
                ),
            ],
        )
        g.add_node("seq1", node=seq_node)

        # Inner VINodes in two frames
        write1_node = VINode(
            id="write1", vi=vi_name, name="Write.vi",
            node_type="iUse",
            parent="seq1", frame="0",
            terminals=[
                Terminal(id="w1_in", index=0, direction="input"),
            ],
        )
        g.add_node("write1", node=write1_node)

        write2_node = VINode(
            id="write2", vi=vi_name, name="Write.vi",
            node_type="iUse",
            parent="seq1", frame="1",
            terminals=[],
        )
        g.add_node("write2", node=write2_node)

        # Upstream VINode
        start_node = VINode(
            id="start", vi=vi_name, name="Start.vi",
            node_type="iUse",
            terminals=[
                Terminal(id="start_out", index=0, direction="output"),
            ],
        )
        g.add_node("start", node=start_node)

        # Wire: start -> seq1 (typed WireEnd edges on MultiDiGraph)
        src = WireEnd(
            terminal_id="start_out", node_id="start",
            index=0, labels=["SubVI"],
        )
        dst = WireEnd(
            terminal_id="tun_outer", node_id="seq1",
            index=0, labels=["FlatSequence"],
        )
        g.add_edge("start", "seq1", source=src, dest=dst, vi=vi_name)

        # Register all node UIDs for this VI
        graph._vi_nodes[vi_name] = {
            vi_name, "seq1", "write1", "write2", "start",
        }
        graph._dep_graph.add_node(vi_name)

        return graph

    def test_sequence_in_operations(
        self, graph_with_sequence: InMemoryVIGraph,
    ):
        """Flat sequence appears as an operation."""
        ops = graph_with_sequence.get_operations("Seq.vi")
        seq_ops = [
            op for op in ops if op.node_type == "flatSequence"
        ]
        assert len(seq_ops) == 1
        assert seq_ops[0].labels == ["FlatSequence"]

    def test_inner_nodes_excluded_from_top_level(
        self, graph_with_sequence: InMemoryVIGraph,
    ):
        """Inner nodes of sequence frames don't appear at top level."""
        ops = graph_with_sequence.get_operations("Seq.vi")
        op_ids = {op.id for op in ops}
        assert "write1" not in op_ids
        assert "write2" not in op_ids

    def test_sequence_has_tunnels(
        self, graph_with_sequence: InMemoryVIGraph,
    ):
        """Sequence operation has tunnel info."""
        ops = graph_with_sequence.get_operations("Seq.vi")
        seq_op = [
            op for op in ops if op.node_type == "flatSequence"
        ][0]
        assert len(seq_op.tunnels) == 1
        assert seq_op.tunnels[0].tunnel_type == "seqTun"

    def test_sequence_has_case_frames(
        self, graph_with_sequence: InMemoryVIGraph,
    ):
        """Sequence frames are stored as case_frames."""
        ops = graph_with_sequence.get_operations("Seq.vi")
        seq_op = [
            op for op in ops if op.node_type == "flatSequence"
        ][0]
        assert len(seq_op.case_frames) == 2
        assert seq_op.case_frames[0].selector_value == "0"
        assert seq_op.case_frames[1].selector_value == "1"

    def test_sequence_after_upstream_dependency(
        self, graph_with_sequence: InMemoryVIGraph,
    ):
        """Sequence appears after nodes that feed into it."""
        ops = graph_with_sequence.get_operations("Seq.vi")
        op_ids = [op.id for op in ops]
        start_idx = op_ids.index("start")
        seq_idx = op_ids.index("seq1")
        assert start_idx < seq_idx


# === Codegen Tests ===


class TestFlatSequenceCodeGen:
    """Tests for FlatSequenceCodeGen."""

    @pytest.fixture
    def codegen(self) -> FlatSequenceCodeGen:
        return FlatSequenceCodeGen()

    def test_empty_frames(self, codegen: FlatSequenceCodeGen):
        """No frames produces empty fragment."""
        op = Operation(
            id="seq1",
            name="Flat Sequence",
            labels=["FlatSequence"],
            case_frames=[],
        )
        ctx = CodeGenContext()
        fragment = codegen.generate(op, ctx)
        assert fragment.statements == []

    def test_sequential_frame_execution(
        self, codegen: FlatSequenceCodeGen,
    ):
        """Frames generate sequential code."""
        # Inner operation that generates a simple assignment
        inner_op = Operation(
            id="prim1",
            name="Add",
            labels=["Primitive"],
            node_type="prim",
            primResID=1,
            terminals=[],
        )

        op = Operation(
            id="seq1",
            name="Flat Sequence",
            labels=["FlatSequence"],
            node_type="flatSequence",
            case_frames=[
                CaseFrame(
                    selector_value="0",
                    inner_node_uids=["prim1"],
                    operations=[inner_op],
                ),
            ],
            tunnels=[],
        )
        ctx = CodeGenContext()
        fragment = codegen.generate(op, ctx)

        # Should produce some statements (even if just a comment
        # for unknown primitives)
        assert len(fragment.statements) > 0

    def test_tunnel_input_binding(
        self, codegen: FlatSequenceCodeGen,
    ):
        """Input tunnels bind outer variable to inner context."""
        from tests.helpers import make_graph_with_edge, make_node

        # Build a graph with src -> tun_outer edge AND tun_inner terminal
        graph = make_graph_with_edge("src", "tun_outer")
        inner_node = make_node("inner_n", ["tun_inner"])
        graph._graph.add_node("inner_n", node=inner_node)
        graph._term_to_node["tun_inner"] = "inner_n"

        ctx = CodeGenContext(graph=graph)
        ctx.bind("src", "task_ref")

        op = Operation(
            id="seq1",
            name="Flat Sequence",
            labels=["FlatSequence"],
            node_type="flatSequence",
            case_frames=[
                CaseFrame(
                    selector_value="0",
                    operations=[],
                ),
            ],
            tunnels=[
                Tunnel(
                    outer_terminal_uid="tun_outer",
                    inner_terminal_uid="tun_inner",
                    tunnel_type="seqTun",
                ),
            ],
        )

        codegen.generate(op, ctx)

        # Inner terminal should resolve to the outer value
        assert ctx.resolve("tun_inner") == "task_ref"

    def test_tunnel_output_binding(
        self, codegen: FlatSequenceCodeGen,
    ):
        """Output tunnels propagate inner values outward."""
        from tests.helpers import make_ctx

        ctx = make_ctx("tun_inner", "tun_outer")
        # Pre-bind what an inner operation would produce
        ctx.bind("tun_inner", "result_val")

        op = Operation(
            id="seq1",
            name="Flat Sequence",
            labels=["FlatSequence"],
            node_type="flatSequence",
            case_frames=[
                CaseFrame(
                    selector_value="0",
                    operations=[],
                ),
            ],
            tunnels=[
                Tunnel(
                    outer_terminal_uid="tun_outer",
                    inner_terminal_uid="tun_inner",
                    tunnel_type="seqTun",
                ),
            ],
        )

        fragment = codegen.generate(op, ctx)

        assert fragment.bindings.get("tun_outer") == "result_val"


# === Codegen registration test ===


class TestCodeGenRegistry:
    """Test that sequence codegens are properly registered."""

    def test_flat_sequence_returns_correct_codegen(self):
        from vipy.agent.codegen.nodes.base import get_codegen

        op = Operation(
            id="1", name="Flat Sequence",
            labels=["FlatSequence"],
            node_type="flatSequence",
        )
        cg = get_codegen(op)
        assert isinstance(cg, FlatSequenceCodeGen)

    def test_stacked_sequence_returns_correct_codegen(self):
        from vipy.agent.codegen.nodes.base import get_codegen

        op = Operation(
            id="1", name="Stacked Sequence",
            labels=["FlatSequence"],
            node_type="seq",
        )
        cg = get_codegen(op)
        assert isinstance(cg, FlatSequenceCodeGen)
