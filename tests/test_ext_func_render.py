"""Call Library Function Node drawing: one row per terminal pair, placed from the
terminals' own geometry, with no text inside the node."""

from __future__ import annotations

import re
from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.render import render_vi
from tests.test_ext_func_terminals import _MAIN_XML

# Node bounds (top, left, bottom, right) = (10, 20, 52, 60): 40 wide. Each row's
# input terminal records its termBounds (top, left, bottom, right); the output
# terminal's rect is the same one mirrored across the node's width.
_BD_XML = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="0" />
    <nodeList elements="1">
      <SL__arrayElement class="extFunc" uid="20">
        <bounds>(10, 20, 52, 60)</bounds>
        <termList elements="9">
          <SL__arrayElement class="term" uid="21">
            <dco class="hGrowCItem" uid="22">
              <objFlags>131072</objFlags>
              <typeDesc>TypeID(22)</typeDesc>
              <termBounds>(0, 0, 9, 13)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="23">
            <dco class="hGrowCItem" uid="24">
              <objFlags>131073</objFlags>
              <typeDesc>TypeID(23)</typeDesc>
              <termBounds>(0, 27, 9, 40)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="25">
            <dco class="hGrowCItem" uid="26">
              <objFlags>131072</objFlags>
              <typeDesc>TypeID(24)</typeDesc>
              <termBounds>(9, 0, 18, 13)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="27">
            <dco class="hGrowCItem" uid="28">
              <objFlags>131073</objFlags>
              <typeDesc>TypeID(25)</typeDesc>
              <termBounds>(9, 27, 18, 40)</termBounds>
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
              <termBounds>(18, 0, 26, 20)</termBounds>
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
              <termBounds>(26, 0, 34, 20)</termBounds>
            </dco>
          </SL__arrayElement>
          <SL__arrayElement class="term" uid="35">
            <dco uid="34" />
          </SL__arrayElement>
        </termList>
        <nodeName>"DoWork"</nodeName>
        <libPath>505448300000000C00010001076C69622E646C6C</libPath>
        <funcTD>1D</funcTD>
      </SL__arrayElement>
    </nodeList>
  </root>
</SL__rootObject>
"""


def _svg(tmp_path: Path) -> str:
    (tmp_path / "Clfn_BDHb.xml").write_text(_BD_XML)
    (tmp_path / "Clfn.xml").write_text(_MAIN_XML.format(return_label=""))
    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "Clfn_BDHb.xml", layout=True)
    svg = render_vi(graph, graph.list_vis()[0], interactive=False)
    assert svg is not None
    return svg


def _node_markup(svg: str) -> str:
    start = svg.index('<g class="lv-node" data-node="20">')
    return svg[start : svg.index("</g>", start)]


def test_rows_and_arrows_sit_where_the_terminals_are(tmp_path: Path) -> None:
    node = _node_markup(_svg(tmp_path))
    # The node is drawn from its top at y=10 and x=20..60. Rows end 9, 18 and
    # 26 below it: path pair, error pair, the return value (output only), then
    # the `name` parameter.
    assert re.findall(r'<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)"', node) == [
        ("20.0", "19.0", "60.0"),
        ("20.0", "28.0", "60.0"),
        ("20.0", "36.0", "60.0"),
    ]
    # An input's arrow sits on the left edge and an output's on the right edge,
    # at the middle of its row: 14.5, 23.5, 32.0 (return value, right only), 40.0.
    assert re.findall(r'<polygon points="([^"]*)"', node) == [
        "21.0,12.5 24.0,14.5 21.0,16.5",
        "56.0,12.5 59.0,14.5 56.0,16.5",
        "21.0,21.5 24.0,23.5 21.0,25.5",
        "56.0,21.5 59.0,23.5 56.0,25.5",
        "56.0,30.0 59.0,32.0 56.0,34.0",
        "21.0,38.0 24.0,40.0 21.0,42.0",
        "56.0,38.0 59.0,40.0 56.0,42.0",
    ]


def test_no_text_is_drawn_inside_the_node(tmp_path: Path) -> None:
    assert "<text" not in _node_markup(_svg(tmp_path))
