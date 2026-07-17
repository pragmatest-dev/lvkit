"""Graph-level test for Disable structure containment.

Regression test for the bug where a Disable structure's inner nodes were
orphaned (parent=None, top-level) because the block-diagram heap's
class="commentNode" was treated as a free-text comment and skipped
entirely. See parser/nodes/disable.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from lvkit.graph import InMemoryVIGraph
from lvkit.models import DisableStructureOperation

_BD_XML = """<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="1">
  <root class="diag" uid="2">
    <zPlaneList>
      <SL__arrayElement class="commentNode" uid="100">
        <termList></termList>
        <diagramList>
          <SL__arrayElement class="diag" uid="110">
            <nodeList>
              <SL__arrayElement class="prim" uid="200">
                <primResID>1</primResID>
                <termList></termList>
              </SL__arrayElement>
            </nodeList>
          </SL__arrayElement>
          <SL__arrayElement class="diag" uid="111">
            <nodeList></nodeList>
          </SL__arrayElement>
        </diagramList>
        <selString class="selLabel">
          <textRec class="textHair"><text>" Disabled "</text></textRec>
        </selString>
        <activeDiag>01</activeDiag>
      </SL__arrayElement>
    </zPlaneList>
  </root>
</SL__rootObject>
"""


def test_disable_structure_inner_node_parented_not_top_level(tmp_path: Path) -> None:
    bd_xml = tmp_path / "DisableTest_BDHb.xml"
    bd_xml.write_text(_BD_XML)

    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    vi_name = graph.list_vis()[0]

    # The inner prim (uid 200) must be parented to the disable structure
    # (uid 100), not top-level.
    q_inner = f"{vi_name}::200"
    inner_node = graph._graph.nodes[q_inner]["node"]  # noqa: SLF001
    assert inner_node.parent == f"{vi_name}::100"
    assert inner_node.frame == "Enabled"

    # It must NOT appear in get_operations() (top-level only).
    top_level_ids = {op.id for op in graph.get_operations(vi_name)}
    assert q_inner not in top_level_ids

    # The disable structure itself IS top-level and carries the frame with
    # the nested prim as an inner operation.
    ops_by_id = {op.id: op for op in graph.get_operations(vi_name)}
    q_struct = f"{vi_name}::100"
    assert q_struct in ops_by_id
    disable_op = cast(DisableStructureOperation, ops_by_id[q_struct])
    assert isinstance(disable_op, DisableStructureOperation)
    frame_by_value = {f.selector_value: f for f in disable_op.frames}
    assert set(frame_by_value) == {"Enabled", "Disabled"}
    enabled_frame = frame_by_value["Enabled"]
    assert [op.id for op in enabled_frame.operations] == [q_inner]
