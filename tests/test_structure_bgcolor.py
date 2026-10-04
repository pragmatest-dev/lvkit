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


def test_structure_node_bg_color_is_on_the_common_base():
    """``bg_color`` lives on ``StructureNode`` itself -- every structure kind
    (loop, case, disable, sequence, event, IPES) inherits it and renders it
    through the SAME ``StructureBodyGlyph.bg_color`` path with no per-kind
    special-casing (a prior version set it only on the loop glyph branches
    in the factory, leaving every other kind always white)."""
    from lvkit.graph.models import CaseStructureNode, InPlaceNode, LoopNode

    assert CaseStructureNode(id="n1", vi_path="v", bg_color="#AABBCC").bg_color == (
        "#AABBCC"
    )
    assert LoopNode(id="n2", vi_path="v", bg_color="#001122").bg_color == "#001122"
    assert InPlaceNode(id="n3", vi_path="v").bg_color is None


def test_first_frame_bg_color_is_the_shared_build_time_helper():
    """Every multi-frame structure's build handler (case/disable/sequence/
    event) stamps ``StructureNode.bg_color`` with its FIRST frame's color via
    this ONE shared helper -- not four separate, independently-written
    per-handler computations."""
    from lvkit.graph.builders.structures import _first_frame_bg_color
    from lvkit.models import CaseFrame

    frames = [
        CaseFrame(selector_value="True", bg_color="#AABBCC"),
        CaseFrame(selector_value="False", bg_color="#001122"),
    ]
    assert _first_frame_bg_color(frames) == "#AABBCC"
    assert _first_frame_bg_color([]) is None


def test_flat_sequence_glyph_draws_one_color_per_compartment():
    """A flat sequence shows every frame SIDE BY SIDE at once -- each
    compartment needs its OWN saved color, not one shared fill for the
    whole structure (verified against a real corpus VI: its first frame
    carries a real, different bgColor than the rest, and LabVIEW's own
    screenshot shows them as visibly distinct cream/white compartments)."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.glyphs.structures.flat_sequence import FlatSequenceGlyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = FlatSequenceGlyph(dividers=[10.0], frame_colors=["#FFFECF", None])
    backend = SvgBackend()
    glyph.draw_body(backend, (0.0, 0.0, 20.0, 10.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 20.0, 10.0))
    assert "#FFFECF" in svg
    assert DEFAULT_THEME.canvas in svg


def test_flat_sequence_glyph_without_frame_colors_uses_default_fill():
    """No frame_colors given (every other structure kind, or a flat sequence
    whose parser pass found nothing) -- falls back to the single-rect
    default body, unchanged from before this feature."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.glyphs.structures.flat_sequence import FlatSequenceGlyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = FlatSequenceGlyph(dividers=[10.0])
    backend = SvgBackend()
    glyph.draw_body(backend, (0.0, 0.0, 20.0, 10.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 20.0, 10.0))
    assert svg.count("<rect") == 1
    assert DEFAULT_THEME.canvas in svg


def test_flat_sequence_divider_band_starts_at_divider_x_not_centered_on_it():
    """A divider's recorded x is the NEXT frame's own left edge -- heap-
    verified on a real corpus VI: two adjacent ``sequenceFrame`` elements'
    own ``<bounds>`` overlap by exactly one ``THICK_FRAME_BORDER_W``, and the
    divider x is the START of that overlap (the left edge of the frame that
    follows it), not its midpoint. So the border band must span
    ``[dx, dx + THICK_FRAME_BORDER_W]`` -- NOT centered on ``dx`` -- or it
    eats into the following frame's own content area."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.glyphs.structures.base import THICK_FRAME_BORDER_W
    from lvkit.render.glyphs.structures.flat_sequence import FlatSequenceGlyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = FlatSequenceGlyph(dividers=[10.0])
    backend = SvgBackend()
    glyph.draw_outline(backend, (0.0, 0.0, 40.0, 20.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 40.0, 20.0))
    assert 'x="10.0" ' in svg
    assert f'width="{THICK_FRAME_BORDER_W}"' in svg


def test_flat_sequence_divider_band_does_not_exceed_rail_band():
    """A divider/edge band must end flush at the rail band's own outer
    edge -- not the raw node bounds -- or it pokes a bare nub above/below
    the rail (the real film-strip rail is inset ``RAIL_INSET`` from the
    node's edge)."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.glyphs.structures.base import RAIL_INSET, THICK_FRAME_BORDER_W
    from lvkit.render.glyphs.structures.flat_sequence import FlatSequenceGlyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = FlatSequenceGlyph(dividers=[10.0])
    backend = SvgBackend()
    y1, y2 = 0.0, 20.0
    glyph.draw_outline(backend, (0.0, y1, 40.0, y2), DEFAULT_THEME)
    svg = backend.render((0.0, y1, 40.0, y2))
    half = THICK_FRAME_BORDER_W / 2
    top = y1 + RAIL_INSET - half
    assert f'y="{top}"' in svg, svg
