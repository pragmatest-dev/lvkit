"""A Flat Sequence inside an In Place Element Structure.

LabVIEW lists a nested Flat Sequence only in the z-plane of the diagram that holds
it, never in that diagram's node list, so the structure's parser must read both
(as every other structure's does). Without the Flat Sequence in the structure, a
node inside the structure that reads the Flat Sequence's output has no value.

The block diagram below has one In Place Element Structure. Inside it, a Flat
Sequence of one frame holds a Random Number (0-1) wired to the sequence's output
tunnel, and an Increment outside the sequence reads that tunnel.
"""

from __future__ import annotations

import ast
import xml.etree.ElementTree as ET
from pathlib import Path

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import InPlaceNode, SequenceNode
from lvkit.parser.nodes.decompose import extract_decompose_structures
from tests.helpers import exec_generated

_BD_XML = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="1">
      <SL__arrayElement class="decomposeRecomposeStructure" uid="100">
        <termList elements="0" />
        <diagramList elements="1">
          <SL__arrayElement class="diag" uid="110">
            <zPlaneList elements="1">
              <SL__arrayElement class="flatSequence" uid="120">
                <sequenceList elements="1">
                  <SL__arrayElement class="sequenceFrame" uid="130">
                    <termList elements="1">
                      <SL__arrayElement class="term" uid="141">
                        <dco class="seqTun" uid="140">
                          <termList elements="2">
                            <SL__arrayElement uid="142" />
                            <SL__arrayElement uid="141" />
                          </termList>
                        </dco>
                      </SL__arrayElement>
                    </termList>
                    <diagramList elements="1">
                      <SL__arrayElement class="diag" uid="150">
                        <zPlaneList elements="1">
                          <SL__arrayElement class="prim" uid="160">
                            <primResID>1070</primResID>
                            <termList elements="1">
                              <SL__arrayElement class="term" uid="161">
                                <dco class="parm" uid="162">
                                  <objFlags>1</objFlags>
                                </dco>
                              </SL__arrayElement>
                            </termList>
                          </SL__arrayElement>
                        </zPlaneList>
                        <nodeList elements="2">
                          <SL__arrayElement class="sRN" uid="170">
                            <termList elements="1">
                              <SL__arrayElement class="term" uid="142">
                                <dco uid="140" />
                              </SL__arrayElement>
                            </termList>
                          </SL__arrayElement>
                          <SL__arrayElement uid="160" />
                        </nodeList>
                        <signalList elements="1">
                          <SL__arrayElement class="signal" uid="180">
                            <termList elements="2">
                              <SL__arrayElement uid="161" />
                              <SL__arrayElement uid="142" />
                            </termList>
                          </SL__arrayElement>
                        </signalList>
                      </SL__arrayElement>
                    </diagramList>
                    <tunnelList elements="1">
                      <SL__arrayElement uid="140" />
                    </tunnelList>
                  </SL__arrayElement>
                </sequenceList>
              </SL__arrayElement>
            </zPlaneList>
            <nodeList elements="1">
              <SL__arrayElement class="prim" uid="200">
                <primResID>1057</primResID>
                <termList elements="2">
                  <SL__arrayElement class="term" uid="201">
                    <dco class="parm" uid="202">
                      <parmIndex>1</parmIndex>
                    </dco>
                  </SL__arrayElement>
                  <SL__arrayElement class="term" uid="203">
                    <dco class="parm" uid="204">
                      <objFlags>1</objFlags>
                    </dco>
                  </SL__arrayElement>
                </termList>
              </SL__arrayElement>
            </nodeList>
            <signalList elements="1">
              <SL__arrayElement class="signal" uid="210">
                <termList elements="2">
                  <SL__arrayElement uid="141" />
                  <SL__arrayElement uid="201" />
                </termList>
              </SL__arrayElement>
            </signalList>
          </SL__arrayElement>
        </diagramList>
      </SL__arrayElement>
    </zPlaneList>
    <nodeList elements="1"><SL__arrayElement uid="100" /></nodeList>
    <signalList elements="0" />
  </root>
</SL__rootObject>
"""


def _load(tmp_path: Path) -> tuple[InMemoryVIGraph, str]:
    bd_xml = tmp_path / "IpeSeq_BDHb.xml"
    bd_xml.write_text(_BD_XML)
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    return graph, graph.list_vis()[0]


def test_parser_reads_the_flat_sequence_into_the_structure() -> None:
    (structure,) = extract_decompose_structures(ET.fromstring(_BD_XML))
    assert structure.inner_node_uids == ["200", "120"]


def test_graph_puts_the_flat_sequence_inside_the_structure(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    sequence = graph.get_graph_node(f"{vi_name}::120")
    assert isinstance(sequence, SequenceNode)
    assert sequence.parent == f"{vi_name}::100"
    structure = graph.get_graph_node(f"{vi_name}::100")
    assert isinstance(structure, InPlaceNode)
    assert [op.id for op in graph.top_level_nodes(vi_name)] == [structure.id]


def test_describe_nests_the_flat_sequence(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    text = describe_vi(graph, vi_name)
    assert "In Place Element\n" in text
    assert "  Flat Sequence" in text


def test_generated_python_runs(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    source = build_module(graph.get_vi_context(vi_name), vi_name, graph=graph)
    tree = ast.parse(source)
    func = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    assert "random.random()" in source
    namespace: dict = {}
    exec_generated(source, namespace, "<ipeseq>")  # noqa: S102
    namespace[func.name]()
