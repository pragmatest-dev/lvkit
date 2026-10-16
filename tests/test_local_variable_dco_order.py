"""A local variable's ``paramIdx`` is the control's dcoIndex -- its position in
the FP heap root's ``<ddoList>`` -- not its position in the z-order walk of
the panel.

Regression: on a panel whose controls had been reordered, every local
variable was named after (and wired to) the wrong control, because paramIdx
was resolved against ``ParsedFrontPanel.controls`` (z-order). In the VI that
exposed it (LabVIEW 25, 30 controls) all 51 local variables were affected,
e.g. a local of a refnum array came out as the graph indicator. A gRefDCO
without ``<paramIdx>`` (the heap omits the default 0) was left unresolved.
"""

from __future__ import annotations

from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import LocalVariableNode
from lvkit.parser.models import ParsedBlockDiagram
from lvkit.parser.vi import _parse_front_panel


def _fpdco(uid: str, label: str) -> str:
    return (
        f'<SL__arrayElement class="fPDCO" uid="{uid}">'
        f'<ddo class="stdBool" uid="{uid}1">'
        "<partsList>"
        f'<SL__arrayElement class="label" uid="{uid}2">'
        f'<textRec class="textHair"><text>"{label}"</text></textRec>'
        "</SL__arrayElement>"
        "</partsList>"
        "</ddo>"
        "</SL__arrayElement>"
    )


# z-order (document order): Alpha, Beta. DCO-table order (<ddoList>): Beta, Alpha.
_FP_XML = (
    '<SL__rootObject class="oHExt" uid="1">'
    '<root class="supC" uid="2">'
    '<ddoList><SL__arrayElement uid="20" /><SL__arrayElement uid="10" /></ddoList>'
    '<paneHierarchy class="pane" uid="3"><zPlaneList>'
    + _fpdco("10", "Alpha")
    + _fpdco("20", "Beta")
    + "</zPlaneList></paneHierarchy>"
    "</root>"
    "</SL__rootObject>"
)

_BD_XML = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList>
      <SL__arrayElement class="fPTerm" uid="100"><dco uid="10" /></SL__arrayElement>
      <SL__arrayElement class="fPTerm" uid="200"><dco uid="20" /></SL__arrayElement>
      <SL__arrayElement class="gRef" uid="300">
        <termList elements="1">
          <SL__arrayElement class="term" uid="301">
            <dco class="gRefDCO" uid="302"><paramIdx>1</paramIdx></dco>
          </SL__arrayElement>
        </termList>
      </SL__arrayElement>
      <SL__arrayElement class="gRef" uid="400">
        <termList elements="1">
          <SL__arrayElement class="term" uid="401">
            <dco class="gRefDCO" uid="402" />
          </SL__arrayElement>
        </termList>
      </SL__arrayElement>
    </zPlaneList>
  </root>
</SL__rootObject>
"""


def test_front_panel_reads_dco_order_from_ddolist(tmp_path: Path) -> None:
    fp_xml = tmp_path / "Locals_FPHb.xml"
    fp_xml.write_text(_FP_XML)
    fp = _parse_front_panel(
        fp_xml, ParsedBlockDiagram(nodes=[], constants=[], wires=[])
    )

    assert [c.uid for c in fp.controls] == ["10", "20"]  # z-order
    assert fp.dco_order == ["20", "10"]  # DCO-table order
    assert fp.control_uid_at_param_idx(0) == "20"
    assert fp.control_uid_at_param_idx(1) == "10"
    assert fp.control_uid_at_param_idx(2) is None


def test_front_panel_without_ddolist_falls_back_to_controls_order(
    tmp_path: Path,
) -> None:
    fp_xml = tmp_path / "Old_FPHb.xml"
    fp_xml.write_text(
        "<root><paneHierarchy>"
        + _fpdco("10", "Alpha")
        + _fpdco("20", "Beta")
        + "</paneHierarchy></root>"
    )
    fp = _parse_front_panel(
        fp_xml, ParsedBlockDiagram(nodes=[], constants=[], wires=[])
    )

    assert fp.dco_order == []
    assert fp.control_uid_at_param_idx(0) == "10"
    assert fp.control_uid_at_param_idx(1) == "20"


def test_local_variables_resolve_through_ddolist(tmp_path: Path) -> None:
    bd_xml = tmp_path / "Locals_BDHb.xml"
    bd_xml.write_text(_BD_XML)
    (tmp_path / "Locals_FPHb.xml").write_text(_FP_XML)

    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    vi = graph.list_vis()[0]
    locals_by_uid = {
        n.id.rsplit("::", 1)[-1]: n
        for n in graph.iter_nodes(vi)
        if isinstance(n, LocalVariableNode)
    }

    # paramIdx 1 -> <ddoList>[1] -> uid 10 -> "Alpha" (z-order would say "Beta")
    assert locals_by_uid["300"].control_name == "Alpha"
    # no <paramIdx> -> 0 -> <ddoList>[0] -> uid 20 -> "Beta"
    assert locals_by_uid["400"].control_name == "Beta"
