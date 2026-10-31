"""Error Cluster From Error Code (vi.lib): a call's terminals take their names
from the connector pane every corpus caller wires (LabVIEW 9.0 to 21.0)."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.vilib_resolver import get_resolver

_TERMINALS = [
    # (uid, index, output?, TypeID)
    ("21", 0, True, 24),
    ("22", 4, False, 20),
    ("23", 5, False, 20),
    ("24", 6, False, 24),
    ("25", 8, False, 21),
    ("26", 9, False, 22),
]


def _terminal(uid: str, index: int, output: bool, type_id: int) -> str:
    flags = "<objFlags>1</objFlags>" if output else ""
    return f"""
      <SL__arrayElement class="term" uid="{uid}">
        <dco class="parm" uid="{int(uid) + 100}">
          {flags}
          <paramIdx>{index}</paramIdx>
          <typeDesc>TypeID({type_id})</typeDesc>
        </dco>
      </SL__arrayElement>"""


_TERMS = "".join(_terminal(*t) for t in _TERMINALS)

_BD_XML = f"""<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList elements="0" />
    <nodeList elements="1">
      <SL__arrayElement class="iUse" uid="20">
        <termList elements="{len(_TERMINALS)}">{_TERMS}
        </termList>
      </SL__arrayElement>
    </nodeList>
  </root>
</SL__rootObject>
"""

_MAIN_XML = """<?xml version='1.0' encoding='utf-8'?>
<LVSR>
  <TM80>
    <!-- TypeID 20: Boolean -->
    <!-- TypeID 21: String -->
    <!-- TypeID 22: NumInt32 -->
    <!-- TypeID 24: Cluster -->
  </TM80>
  <LIbd>
    <Section>
      <BDHP>
        <IUVI LinkSaveFlag="0">
          <LinkSaveQualName>
            <String>Error Cluster From Error Code.vi</String>
          </LinkSaveQualName>
          <LinkSavePathRef>
            <String>&lt;vilib&gt;</String>
            <String>Utility</String>
            <String>error.llb</String>
            <String>Error Cluster From Error Code.vi</String>
          </LinkSavePathRef>
          <LinkOffsetList><Offset>0x14</Offset></LinkOffsetList>
        </IUVI>
      </BDHP>
    </Section>
  </LIbd>
</LVSR>
"""


def _graph(tmp_path: Path) -> tuple[InMemoryVIGraph, str]:
    (tmp_path / "Caller_BDHb.xml").write_text(_BD_XML)
    (tmp_path / "Caller.xml").write_text(_MAIN_XML)
    graph = InMemoryVIGraph()
    graph.load_vi(tmp_path / "Caller_BDHb.xml")
    return graph, graph.list_vis()[0]


def test_resolver_terminal_list() -> None:
    entry = get_resolver().resolve_by_name("Error Cluster From Error Code.vi")
    assert entry is not None
    assert [(t.index, t.name, t.direction, t.type) for t in entry.terminals] == [
        (4, "is warning? (False)", "input", "Boolean"),
        (5, "show call chain? (False)", "input", "Boolean"),
        (6, "error in (no error)", "input", "Cluster"),
        (8, 'error message ("")', "input", "String"),
        (9, "error code (0)", "input", "NumInt32"),
        (0, "error out", "output", "Cluster"),
    ]


def test_the_call_terminal_list(tmp_path: Path) -> None:
    graph, vi_name = _graph(tmp_path)
    node = graph._graph.nodes[f"{vi_name}::20"]["node"]
    assert [
        (t.index, t.direction, t.lv_type.underlying_type) for t in node.terminals
    ] == [
        (0, "output", "Cluster"),
        (4, "input", "Boolean"),
        (5, "input", "Boolean"),
        (6, "input", "Cluster"),
        (8, "input", "String"),
        (9, "input", "NumInt32"),
    ]
    assert "Error Cluster From Error Code.vi" in describe_vi(graph, vi_name)
    text = render_lvnet(build_netlist_from_graph(graph, vi_name), verbose=True)
    assert "in   is warning? (False)      : TF      default False" in text
    assert "in   show call chain? (False) : TF      default False" in text
    assert "in   error in (no error)      : cluster default" in text
    assert 'in   error message ("")       : String  default ""' in text
    assert "in   error code (0)           : I32     default 0" in text
    assert "out  error out                : cluster" in text
