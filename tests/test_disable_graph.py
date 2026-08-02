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


def test_get_operations_does_not_mutate_persistent_frames(tmp_path: Path) -> None:
    """``get_operations()`` builds a fresh Operation view -- it must NOT write
    frame contents back onto the persistent ``DisableStructureNode``. Before
    the fix, ``_populate_frame_operations`` (operations.py) mutated the SAME
    ``Frame`` objects shared with ``gnode.frames`` in place: construction
    seeds ``inner_node_uids`` with RAW (unqualified) parser uids (e.g.
    ``"200"``), while the graph-derived value used for the Operation view is
    QUALIFIED (``"<vi>::200"``) -- so the old code detectably overwrote
    ``gnode.frames[...].inner_node_uids`` with the qualified form as a side
    effect of a plain getter. A getter must not mutate the graph it reads."""
    bd_xml = tmp_path / "DisableTest_BDHb.xml"
    bd_xml.write_text(_BD_XML)

    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    vi_name = graph.list_vis()[0]
    q_struct = f"{vi_name}::100"
    q_inner = f"{vi_name}::200"

    struct_gnode = graph._graph.nodes[q_struct]["node"]  # noqa: SLF001
    pristine_frames = {f.selector_value: f for f in struct_gnode.frames}
    assert set(pristine_frames) == {"Enabled", "Disabled"}
    # Construction-time shells: parser-set (raw, unqualified) inner_node_uids,
    # no operations populated yet.
    assert pristine_frames["Enabled"].inner_node_uids == ["200"]
    assert pristine_frames["Disabled"].inner_node_uids == []
    for frame in struct_gnode.frames:
        assert frame.operations == []

    ops = graph.get_operations(vi_name)

    # The persistent graph node's frames must be untouched by the getter.
    for frame in struct_gnode.frames:
        assert frame.operations == []
    post_frames = {f.selector_value: f for f in struct_gnode.frames}
    assert post_frames["Enabled"].inner_node_uids == ["200"]
    assert post_frames["Disabled"].inner_node_uids == []

    # The RETURNED Operation view IS populated, with graph-qualified uids.
    ops_by_id = {op.id: op for op in ops}
    disable_op = cast(DisableStructureOperation, ops_by_id[q_struct])
    view_frames = {f.selector_value: f for f in disable_op.frames}
    assert view_frames["Enabled"].inner_node_uids == [q_inner]
    assert [op.id for op in view_frames["Enabled"].operations] == [q_inner]

    # Idempotent: a second call returns an equivalent view (no drift), and
    # still leaves gnode.frames untouched.
    ops2 = graph.get_operations(vi_name)
    disable_op2 = cast(
        DisableStructureOperation, {op.id: op for op in ops2}[q_struct],
    )
    assert disable_op2.model_dump() == disable_op.model_dump()
    for frame in struct_gnode.frames:
        assert frame.operations == []
