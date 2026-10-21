"""A terminal's direction comes from its dco's flags, wherever the dco is defined."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.vi import _process_element_terminals

# Delete From Array as saved: each dco is defined once on the node (delDCO,
# dcoAgg, ...) and the termList holds only bare <dco uid=.../> references.
_DELETE_FROM_ARRAY = """
<SL__arrayElement class="aDelete" uid="353">
  <srcDCO4 class="aDelDCO" uid="359"><objFlags>131072</objFlags></srcDCO4>
  <lenDCO class="aDelDCO" uid="362"><objFlags>262400</objFlags></lenDCO>
  <delDCO class="aDelDCO" uid="356"><objFlags>262401</objFlags></delDCO>
  <dcoList elements="1">
    <SL__arrayElement class="aDelDCO" uid="368"><objFlags>6291456</objFlags>
    </SL__arrayElement>
  </dcoList>
  <dcoAgg class="aDelDCO" uid="365"><objFlags>1</objFlags></dcoAgg>
  <termList elements="5">
    <SL__arrayElement class="term" uid="355"><dco uid="356" /></SL__arrayElement>
    <SL__arrayElement class="term" uid="358"><dco uid="359" /></SL__arrayElement>
    <SL__arrayElement class="term" uid="361"><dco uid="362" /></SL__arrayElement>
    <SL__arrayElement class="term" uid="364"><dco uid="365" /></SL__arrayElement>
    <SL__arrayElement class="term" uid="367"><dco uid="368" /></SL__arrayElement>
  </termList>
</SL__arrayElement>
"""


def test_unwired_terminals_take_direction_from_the_node_level_dco() -> None:
    terminal_info: dict = {}
    _process_element_terminals(
        ET.fromstring(_DELETE_FROM_ARRAY), set(), set(), None, terminal_info, {}
    )
    directions = {
        uid: ("out" if info.is_output else "in") for uid, info in terminal_info.items()
    }
    assert directions == {
        "355": "out",  # deleted portion
        "358": "in",  # array
        "361": "in",  # length
        "364": "out",  # output array
        "367": "in",  # index
    }
