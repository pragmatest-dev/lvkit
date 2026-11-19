"""A loop's border terminals are found by CLASS in the layout, as in the parser:
a While Loop's stop terminal is ``loopTestDCO`` in most VIs and ``loopEndDCO``
in others, and either way the layout carries its glyph geometry."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lvkit.parser.layout import build_layout_from_root


def _loop_root(stop_tag: str, count_tag: str = "loopLimitDCO") -> ET.Element:
    return ET.fromstring(
        f"""
        <root>
          <zPlaneList>
            <SL__arrayElement class="whileLoop" uid="428">
              <bounds>(0, 0, 100, 200)</bounds>
              <loopIndexDCO class="lCnt" uid="431">
                <termBounds>(80, 10, 96, 26)</termBounds>
              </loopIndexDCO>
              <{stop_tag} class="lTst" uid="438">
                <termBounds>(80, 170, 96, 186)</termBounds>
              </{stop_tag}>
              <{count_tag} uid="440" />
              <termList></termList>
            </SL__arrayElement>
          </zPlaneList>
        </root>
        """
    )


@pytest.mark.parametrize("stop_tag", ["loopTestDCO", "loopEndDCO"])
def test_stop_terminal_glyph_found_by_class(stop_tag):
    layout = build_layout_from_root(_loop_root(stop_tag))
    assert layout.border_terminal_kind["438"] == "cond"
    assert layout.border_terminal_kind["431"] == "i"
    assert layout.border_terminals["438"] == (170.0, 80.0, 186.0, 96.0)
    assert layout.structure_border_uids["428"] == ["431", "438"]


def test_count_terminal_is_not_a_border_glyph():
    """N is a graph Terminal; neither its class form nor its bare reference
    adds a second glyph."""
    layout = build_layout_from_root(_loop_root("loopTestDCO"))
    assert "N" not in layout.border_terminal_kind.values()
