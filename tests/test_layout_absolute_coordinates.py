"""A VI saved before LabVIEW 8.6 records every ``bounds`` and ``termBounds`` in
one diagram frame; a later VI records them relative to the enclosing structure.
The layout places a nested loop's frame and its border terminals either way."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit.parser.layout import absolute_coordinates, build_layout_from_root

# An outer loop at (100, 50)-(900, 450) holding an inner While Loop whose stop
# terminal is saved as ``loopEndDCO class="lTst"``. Rects are (top, left, bottom,
# right). ``inner`` is the inner loop's rect and ``stop`` its stop terminal's.
_ROOT = """
<root>
  <zPlaneList>
    <SL__arrayElement class="whileLoop" uid="1">
      <bounds>(50, 100, 450, 900)</bounds>
      <diagramList>
        <SL__arrayElement class="diag" uid="2">
          <zPlaneList>
            <SL__arrayElement class="whileLoop" uid="3">
              <bounds>{inner}</bounds>
              <loopIndexDCO class="lCnt" uid="4">
                <termBounds>{index}</termBounds>
              </loopIndexDCO>
              <loopEndDCO class="lTst" uid="5">
                <termBounds>{stop}</termBounds>
              </loopEndDCO>
              <termList></termList>
            </SL__arrayElement>
          </zPlaneList>
        </SL__arrayElement>
      </diagramList>
      <termList></termList>
    </SL__arrayElement>
  </zPlaneList>
</root>
"""


def test_absolute_loop_frame_and_border_terminals():
    root = ET.fromstring(
        _ROOT.format(
            inner="(150, 200, 400, 600)",
            index="(380, 210, 396, 226)",
            stop="(380, 570, 396, 586)",
        )
    )
    layout = build_layout_from_root(root, absolute_coordinates=True)
    assert layout.node_bounds["3"] == (200.0, 150.0, 600.0, 400.0)
    assert layout.border_terminals["5"] == (570.0, 380.0, 586.0, 396.0)
    assert layout.border_terminals["4"] == (210.0, 380.0, 226.0, 396.0)
    assert layout.structure_border_uids["3"] == ["4", "5"]


def test_relative_loop_frame_and_border_terminals_unchanged():
    root = ET.fromstring(
        _ROOT.format(
            inner="(150, 200, 400, 600)",
            index="(230, 10, 246, 26)",
            stop="(230, 370, 246, 386)",
        )
    )
    layout = build_layout_from_root(root)
    assert layout.node_bounds["3"] == (300.0, 200.0, 700.0, 450.0)
    assert layout.border_terminals["5"] == (670.0, 430.0, 686.0, 446.0)


@pytest.mark.parametrize(
    ("major", "minor", "expected"),
    [(8, 2, True), (8, 5, True), (8, 6, False), (11, 0, False)],
)
def test_absolute_coordinates_by_saved_version(
    tmp_path: Path, major: int, minor: int, expected: bool
):
    main = tmp_path / "vi.xml"
    main.write_text(
        f'<LVSR><vers><Section><Version Major="{major}" Minor="{minor}"/>'
        "</Section></vers></LVSR>",
        encoding="utf-8",
    )
    assert absolute_coordinates(main) is expected


def test_absolute_coordinates_false_without_main_xml(tmp_path: Path):
    assert absolute_coordinates(None) is False
    assert absolute_coordinates(tmp_path / "missing.xml") is False


# A flat sequence of two frames; frame 1 sits 300 to the right of frame 0 and
# its flat sequence tunnel is saved in the diagram frame (rects are top, left,
# bottom, right).
_FLAT_SEQUENCE = """
<root>
  <zPlaneList>
    <SL__arrayElement class="flatSequence" uid="1">
      <bounds>(50, 100, 250, 700)</bounds>
      <sequenceList>
        <SL__arrayElement class="sequenceFrame" uid="2">
          <bounds>(50, 100, 250, 400)</bounds>
          <termList>
            <SL__arrayElement class="term" uid="3">
              <dco class="flatSeqTun" uid="4">
                <termBounds>(120, 390, 130, 400)</termBounds>
              </dco>
            </SL__arrayElement>
          </termList>
        </SL__arrayElement>
        <SL__arrayElement class="sequenceFrame" uid="5">
          <bounds>(50, 400, 250, 700)</bounds>
          <termList>
            <SL__arrayElement class="term" uid="6">
              <dco class="flatSeqTun" uid="7">
                <termBounds>(120, 400, 130, 410)</termBounds>
              </dco>
            </SL__arrayElement>
          </termList>
        </SL__arrayElement>
      </sequenceList>
    </SL__arrayElement>
  </zPlaneList>
</root>
"""


def test_absolute_flat_sequence_tunnels_stay_in_the_diagram_frame():
    layout = build_layout_from_root(
        ET.fromstring(_FLAT_SEQUENCE), absolute_coordinates=True
    )
    assert layout.terminal_centers["3"] == (395.0, 125.0)
    assert layout.terminal_centers["6"] == (405.0, 125.0)
