"""A Property Node has one row per ``propItem`` of its ``dcoList``."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.node_types import PropertyNodeHandler

# The row (uid 242) is also named by a <dco> reference that carries the row's
# class; that reference is the same row, not a second one.
_NODE = """<SL__arrayElement class="propNode" uid="227">
  <nodeName>"LVClassLibrary"</nodeName>
  <dcoList elements="1">
    <SL__arrayElement class="propItem" uid="242">
      <propList elements="1">
        <SL__arrayElement class="propItemInfo" uid="245">
          <PropItemName>"Name"</PropItemName>
          <PropItemCode>5</PropItemCode>
        </SL__arrayElement>
      </propList>
      <termList elements="1"><SL__arrayElement uid="241" /></termList>
    </SL__arrayElement>
  </dcoList>
  <termList elements="2">
    <SL__arrayElement class="term" uid="229" index="0">
      <dco uid="230" />
    </SL__arrayElement>
    <SL__arrayElement class="term" uid="241" index="1">
      <dco class="propItem" uid="242" />
    </SL__arrayElement>
  </termList>
</SL__arrayElement>"""


def test_a_row_named_by_a_terminals_dco_reference_is_one_property() -> None:
    node = PropertyNodeHandler().parse(ET.fromstring(_NODE))
    assert [[(s.name, s.code) for s in p.segments] for p in node.properties] == [
        [("Name", 5)]
    ]
    assert node.dco_terminal_uids == ["241"]
