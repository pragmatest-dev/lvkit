"""A structure's own saved background fill ("right-click > Background Color"
in the editor) is a real LabVIEW feature -- confirmed via a proper
``ElementTree`` parent-tracking walk of a real corpus VI: ``bgColor`` lives on
each structure's own inner ``<diag>`` (or, for a flat-sequence frame, its
nested ``<diag>``), DIRECT CHILD only -- never a descendant's, which could
belong to a nested comment/attachment box instead (those carry their own
``bgColor`` too, confirmed in the same corpus VI, and a ``.//`` search would
wrongly pick one up). Decoded via ``parser.utils.heap_color`` (the same
``00RRGGBB`` convention already used for front-panel part colors).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.nodes.case import extract_case_structures
from lvkit.parser.nodes.disable import extract_disable_structures
from lvkit.parser.nodes.event import extract_event_structures
from lvkit.parser.nodes.loop import extract_loops
from lvkit.parser.nodes.sequence import extract_flat_sequences


def test_loop_bg_color_read_from_inner_diag_direct_child():
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="whileLoop" uid="698">
            <termList></termList>
            <diagramList>
              <SL__arrayElement class="diag" uid="702">
                <nodeList></nodeList>
                <zPlaneList>
                  <SL__arrayElement class="attachment" uid="5">
                    <bgColor>00FEFFD7</bgColor>
                  </SL__arrayElement>
                </zPlaneList>
                <bgColor>0080C0FF</bgColor>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
        </root>
        """
    )
    loops = extract_loops(root)
    assert len(loops) == 1
    # The loop's OWN diag bgColor, not the nested attachment's.
    assert loops[0].bg_color == "#80C0FF"


def test_loop_bg_color_none_when_absent():
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="whileLoop" uid="698">
            <termList></termList>
            <diagramList>
              <SL__arrayElement class="diag" uid="702">
                <nodeList></nodeList>
                <zPlaneList></zPlaneList>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
        </root>
        """
    )
    loops = extract_loops(root)
    assert loops[0].bg_color is None


def test_case_frame_bg_color():
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="select" uid="1">
            <termList></termList>
            <diagramList>
              <SL__arrayElement class="diag" uid="10">
                <nodeList></nodeList>
                <bgColor>00FF8080</bgColor>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
        </root>
        """
    )
    cases = extract_case_structures(root)
    assert len(cases) == 1
    assert cases[0].frames[0].bg_color == "#FF8080"


def test_event_frame_bg_color():
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="eventStruct" uid="1">
            <termList></termList>
            <diagramList>
              <SL__arrayElement class="diag" uid="10">
                <nodeList></nodeList>
                <bgColor>00A0FFA0</bgColor>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
        </root>
        """
    )
    events = extract_event_structures(root)
    assert len(events) == 1
    assert events[0].frames[0].bg_color == "#A0FFA0"


def test_disable_frame_bg_color():
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="commentNode" uid="1">
            <termList></termList>
            <diagramList>
              <SL__arrayElement class="diag" uid="10">
                <nodeList></nodeList>
                <bgColor>00C0C0FF</bgColor>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
        </root>
        """
    )
    disables = extract_disable_structures(root)
    assert len(disables) == 1
    assert disables[0].frames[0].bg_color == "#C0C0FF"


def test_stacked_sequence_frame_bg_color():
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="sequence" uid="1">
            <termList></termList>
            <diagramList>
              <SL__arrayElement class="diag" uid="10">
                <nodeList></nodeList>
                <bgColor>00FFE0A0</bgColor>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
        </root>
        """
    )
    seqs = extract_flat_sequences(root)
    assert len(seqs) == 1
    assert seqs[0].frames[0].bg_color == "#FFE0A0"


def test_while_loop_glyph_fills_with_bg_color():
    """The glyph-level wiring: a While Loop's own custom ``bg_color``
    actually reaches the drawn body fill, not just the parsed data."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.glyphs.structures.factory import structure_body_glyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = structure_body_glyph("whileLoop", bg_color="#80C0FF")
    backend = SvgBackend()
    glyph.draw(backend, (0.0, 0.0, 100.0, 60.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 100.0, 60.0))
    assert "#80C0FF" in svg
    assert DEFAULT_THEME.canvas not in svg


def test_while_loop_glyph_default_fill_when_no_bg_color():
    from lvkit.render.backend import SvgBackend
    from lvkit.render.glyphs.structures.factory import structure_body_glyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = structure_body_glyph("whileLoop")
    backend = SvgBackend()
    glyph.draw(backend, (0.0, 0.0, 100.0, 60.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 100.0, 60.0))
    assert DEFAULT_THEME.canvas in svg


def test_flat_sequence_frame_bg_color_from_nested_diag():
    # A flatSequence frame is a sequenceFrame wrapping its OWN diagramList ->
    # diag -- the bgColor lives on THAT nested diag, not the sequenceFrame
    # itself (see _frame_bg_color's docstring).
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="flatSequence" uid="1">
            <termList></termList>
            <sequenceList>
              <SL__arrayElement class="sequenceFrame" uid="10">
                <bounds>(0, 0, 100, 100)</bounds>
                <diagramList>
                  <SL__arrayElement class="diag" uid="11">
                    <nodeList></nodeList>
                    <bgColor>00D0D0FF</bgColor>
                    </SL__arrayElement>
                  </diagramList>
                </SL__arrayElement>
              </sequenceList>
            </SL__arrayElement>
        </root>
        """
    )
    seqs = extract_flat_sequences(root)
    assert len(seqs) == 1
    assert seqs[0].frames[0].bg_color == "#D0D0FF"
