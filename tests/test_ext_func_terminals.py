"""Call Library Function Node terminals (class="extFunc").

The block diagram below is a node with a path pair, an error pair, the return
value, a parameter wired on both sides and a parameter wired on neither. Each
parameter row is one ``extFuncArg`` DCO owned by its input terminal; the output
terminal holds a bare reference to it. The prototype is the node's ``funcTD``
Cluster in the main XML.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.models import GraphNode
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.parser.extfunc import extfunc_function_name
from lvkit.primitive_resolver import PrimitiveResolutionNeeded
from lvkit.unresolved import UnresolvedItem, _item_key, format_unresolved_report
from tests.test_lvnet_identity import _assert_graph_identity

_BD_XML = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="0" />
    <nodeList elements="3">
      <SL__arrayElement class="prim" uid="10">
        <primResID>1711</primResID>
        <termList elements="1">
          <SL__arrayElement class="term" uid="11">
            <objFlags>1</objFlags>
            <dco class="parm" uid="12"><objFlags>1</objFlags></dco>
          </SL__arrayElement>
        </termList>
      </SL__arrayElement>
      <SL__arrayElement class="prim" uid="50">
        <primResID>1710</primResID>
        <termList elements="1">
          <SL__arrayElement class="term" uid="51">
            <dco class="parm" uid="52"><parmIndex>2</parmIndex></dco>
          </SL__arrayElement>
        </termList>
      </SL__arrayElement>
      <SL__arrayElement class="extFunc" uid="20">
        <bounds>(0, 0, 50, 50)</bounds>
        <termList elements="10">
          <SL__arrayElement class="term" uid="21">
            <dco class="hGrowCItem" uid="22">
              <objFlags>131072</objFlags>
              <typeDesc>TypeID(22)</typeDesc>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="23">
            <dco class="hGrowCItem" uid="24">
              <objFlags>131073</objFlags>
              <typeDesc>TypeID(23)</typeDesc>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="25">
            <dco class="hGrowCItem" uid="26">
              <objFlags>131072</objFlags>
              <typeDesc>TypeID(24)</typeDesc>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="27">
            <dco class="hGrowCItem" uid="28">
              <objFlags>131073</objFlags>
              <typeDesc>TypeID(25)</typeDesc>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="30">
            <dco class="extFuncArg" uid="31">
              <objFlags>65536</objFlags>
              <termList elements="2">
                <SL__arrayElement uid="30" />
                <SL__arrayElement uid="32" />
              </termList>
              <typeDesc>TypeID(20)</typeDesc>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="32">
            <dco uid="31" />
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="33">
            <dco class="extFuncArg" uid="34">
              <termList elements="2">
                <SL__arrayElement uid="33" />
                <SL__arrayElement uid="35" />
              </termList>
              <typeDesc>TypeID(21)</typeDesc>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="35">
            <dco uid="34" />
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="36">
            <dco class="extFuncArg" uid="37">
              <termList elements="2">
                <SL__arrayElement uid="36" />
                <SL__arrayElement uid="38" />
              </termList>
              <typeDesc>TypeID(26)</typeDesc>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="38">
            <dco uid="37" />
          </SL__arrayElement>
        </termList>
        <nodeName>"DoWork"</nodeName>
        <libPath>505448300000000C00010001076C69622E646C6C</libPath>
        <funcTD>1D</funcTD>
      </SL__arrayElement>
    </nodeList>
    <signalList elements="2">
      <SL__arrayElement class="signal" uid="40">
        <termList elements="2">
          <SL__arrayElement uid="11" />
          <SL__arrayElement uid="33" />
        </termList>
      </SL__arrayElement>
      <SL__arrayElement class="signal" uid="41">
        <termList elements="2">
          <SL__arrayElement uid="35" />
          <SL__arrayElement uid="51" />
        </termList>
      </SL__arrayElement>
    </signalList>
  </root>
</SL__rootObject>
"""

# Heap TypeIDs 20 (I32) and 21 (String) are the wire types; TypeID 29 is the
# prototype Cluster over flat types 0-2 (return value, ``name``, ``count``).
# The type the third row's DCO references is labelled ``retries``, which wins
# over the prototype's ``count``.
_MAIN_XML = """<?xml version='1.0' encoding='utf-8'?>
<LVSR>
  <TM80>
    <!-- TypeID 20: NumInt32 -->
    <!-- TypeID 21: String -->
    <!-- Heap TypeID 22 = Consolidated TypeID 2: Path -->
    <!-- Heap TypeID 23 = Consolidated TypeID 3: Path -->
    <!-- Heap TypeID 24 = Consolidated TypeID 4: NumInt32 -->
    <!-- Heap TypeID 25 = Consolidated TypeID 5: NumInt32 -->
    <!-- Heap TypeID 26 = Consolidated TypeID 6: NumInt32 -->
    <!-- Heap TypeID 29 = Consolidated TypeID 1: Cluster -->
  </TM80>
  <VCTP>
    <Section Index="0" Format="inline">
      <TypeDesc Type="NumInt32" Format="inline"{return_label} />
      <TypeDesc Type="String" Format="inline" Label="name" />
      <TypeDesc Type="NumInt32" Format="inline" Label="count" />
      <TypeDesc Type="Cluster" Format="inline">
        <TypeDesc TypeID="0" />
        <TypeDesc TypeID="1" />
        <TypeDesc TypeID="2" />
      </TypeDesc>
      <TypeDesc Type="Path" Format="inline" Label="path in" />
      <TypeDesc Type="Path" Format="inline" Label="path out" />
      <TypeDesc Type="NumInt32" Format="inline" Label="error in (no error)" />
      <TypeDesc Type="NumInt32" Format="inline" Label="error out" />
      <TypeDesc Type="NumInt32" Format="inline" Label="retries" />
      <TopLevel>
        <TypeDesc Index="1" FlatTypeID="3" />
        <TypeDesc Index="2" FlatTypeID="4" />
        <TypeDesc Index="3" FlatTypeID="5" />
        <TypeDesc Index="4" FlatTypeID="6" />
        <TypeDesc Index="5" FlatTypeID="7" />
        <TypeDesc Index="6" FlatTypeID="8" />
      </TopLevel>
    </Section>
  </VCTP>
</LVSR>
"""


def _load(tmp_path: Path, return_label: str = "") -> tuple[InMemoryVIGraph, str]:
    (tmp_path / "Clfn_BDHb.xml").write_text(_BD_XML)
    (tmp_path / "Clfn.xml").write_text(_MAIN_XML.format(return_label=return_label))
    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "Clfn_BDHb.xml")
    return graph, graph.list_vis()[0]


def _clfn(graph: InMemoryVIGraph, vi_name: str) -> GraphNode:
    return graph._graph.nodes[f"{vi_name}::20"]["node"]  # noqa: SLF001


def test_terminal_list(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    terminals = [
        (t.index, t.direction, t.name, t.lv_type.underlying_type if t.lv_type else None)
        for t in _clfn(graph, vi_name).terminals
    ]
    assert terminals == [
        (0, "input", "path in", "Path"),
        (1, "output", "path out", "Path"),
        (2, "input", "error in (no error)", "NumInt32"),
        (3, "output", "error out", "NumInt32"),
        (5, "output", "return value", "NumInt32"),
        (6, "input", "name", "String"),
        (7, "output", "name output", "String"),
        (8, "input", "retries", "NumInt32"),
        (9, "output", "retries output", "NumInt32"),
    ]


def test_named_return_value_keeps_its_prototype_name(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path, ' Label="error code"')
    names = {t.index: t.name for t in _clfn(graph, vi_name).terminals}
    assert names[5] == "error code"


def test_describe(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    assert describe_vi(graph, vi_name).endswith(
        "## Operations\nString To IP [prim 1711]\nlib.dll:DoWork\n"
        "IP To String [prim 1710]"
    )


def test_lvnet_verbose(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    text = render_lvnet(build_netlist_from_graph(graph, vi_name), verbose=True)
    assert text.endswith(
        "    function String_To_IP_10 : String To IP\n"
        "      out  net_address : String\n"
        "    function lib.dll:DoWork_20 : lib.dll:DoWork\n"
        "      in   path in             : Path   default\n"
        "      in   error in (no error) : I32    default 0\n"
        "      in   name                : String = String_To_IP_10::net_address\n"
        "      in   retries             : I32    default 0\n"
        "      out  path out            : Path\n"
        "      out  error out           : I32\n"
        "      out  return value        : I32\n"
        "      out  name output         : String\n"
        "      out  retries output      : I32\n"
        "    function IP_To_String_50 : IP To String\n"
        "      in   net_address : String = lib.dll:DoWork_20::name output"
    )


def test_generate_names_the_node(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    with pytest.raises(PrimitiveResolutionNeeded) as raised:
        build_module(graph.get_vi_context(vi_name), vi_name, graph=graph)
    assert str(raised.value) == (
        "Call Library Function Node lib.dll:DoWork has no code generation yet.\n"
        "  In VI: Clfn.vi\n"
        "  Terminals:\n"
        "    - index=0 direction=input type=Path UNWIRED name=path in\n"
        "    - index=1 direction=output type=Path UNWIRED name=path out\n"
        "    - index=2 direction=input type=NumInt32 UNWIRED name=error in (no error)\n"
        "    - index=3 direction=output type=NumInt32 UNWIRED name=error out\n"
        "    - index=5 direction=output type=NumInt32 UNWIRED name=return value\n"
        "    - index=6 direction=input type=String wired name=name\n"
        "    - index=7 direction=output type=String wired name=name output\n"
        "    - index=8 direction=input type=NumInt32 UNWIRED name=retries\n"
        "    - index=9 direction=output type=NumInt32 UNWIRED name=retries output"
    )


_MUX_BD_XML = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="0" />
    <nodeList elements="1">
      <SL__arrayElement class="mux" uid="60">
        <bounds>(0, 0, 50, 50)</bounds>
        <termList elements="1">
          <SL__arrayElement class="term" uid="61">
            <dco class="mxDCO" uid="62" />
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="63">
            <dco uid="62" />
          </SL__arrayElement>
        </termList>
      </SL__arrayElement>
    </nodeList>
  </root>
</SL__rootObject>
"""


def test_bare_dco_reference_of_a_bundle_stays_an_input(tmp_path: Path) -> None:
    (tmp_path / "Mux_BDHb.xml").write_text(_MUX_BD_XML)
    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "Mux_BDHb.xml")
    vi_name = graph.list_vis()[0]
    node = graph._graph.nodes[f"{vi_name}::60"]["node"]  # noqa: SLF001
    assert [(t.index, t.direction) for t in node.terminals] == [
        (0, "input"),
        (1, "input"),
    ]


def test_unresolved_report_names_the_node(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    sink: list[Exception] = []
    build_module(
        graph.get_vi_context(vi_name),
        vi_name,
        graph=graph,
        soft_unresolved=True,
        unresolved_sink=sink,
    )
    kind, identifier, name = _item_key(sink[0])
    report = format_unresolved_report(
        [UnresolvedItem(kind, identifier, name, ["Clfn.vi"], 1)], "Clfn.vi"
    )
    assert report == (
        "# Unresolved dependencies in Clfn.vi\n\n"
        "1 distinct gap(s): 0 unknown primitive(s), 0 placeholder primitive(s), "
        "1 node(s) with no code generation, 0 unmapped vi.lib VI(s), "
        "0 terminal-mapping gap(s).\n\n"
        "## Nodes with no code generation yet\n"
        "- Call Library Function Node lib.dll:DoWork — 1x in 1 VI(s)\n"
        "    · Clfn.vi"
    )


def test_lvnet_round_trip_keeps_the_graph_identity(tmp_path: Path) -> None:
    graph, vi_name = _load(tmp_path)
    module = build_netlist_from_graph(graph, vi_name)
    text = render_lvnet(module, display_name="Clfn.vi", verbose=True)
    _assert_graph_identity(module, reconstruct_module(parse_lvnet(text)), "Clfn.vi")


def test_function_name_without_a_library_path_is_the_function() -> None:
    elem = ET.fromstring(
        '<SL__arrayElement><nodeName>"Run"</nodeName>'
        "<libPath>505448300000000400020000</libPath></SL__arrayElement>"
    )
    assert extfunc_function_name(elem) == "Run"
