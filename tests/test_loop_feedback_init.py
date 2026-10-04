"""A Feedback Node's initializer terminal, "moved one loop out" (LabVIEW's
own right-click menu item on the node's initializer), relocates from the
Feedback Node's own icon onto the OWNING LOOP's border -- confirmed via NI's
public docs (the Initializer Terminal page, plus community documentation of
"Move Initializer One Loop Out": the terminal becomes "an icon that changes
color depending on the data type... described as a 'dot'... similar to a
shift register"). In the heap this shows up as a ``dco class="initFeedback"``
living directly in the LOOP's own termList (not inside a hiddenFBNode/
slaveFBInputNode pair), linked to its Feedback Node's write/slave side via
``<rsrDCO>`` -- the same cross-reference field a shift register's own
lSR->rSR link uses.

Modeled as a Tunnel like any other loop-border terminal (not a parallel,
bespoke field) -- the one wrinkle is there's no real inner-face uid to pair
it with (the actual consumer is the Feedback Node's own slave terminal,
already modeled elsewhere; reusing its uid here would collide with its own
term_lookup entry), so a synthetic, never-referenced inner uid is used.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.models import Tunnel
from lvkit.parser.nodes.loop import extract_loops


def test_initfeedback_border_terminal_becomes_a_tunnel():
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="whileLoop" uid="989">
            <termList>
              <SL__arrayElement class="term" uid="1030">
                <dco class="initFeedback" uid="1031">
                  <typeDesc>TypeID(85)</typeDesc>
                  <rsrDCO uid="1486" />
                  <iFeedbackLoop uid="989" />
                  </dco>
                </SL__arrayElement>
              </termList>
            <diagramList>
              <SL__arrayElement class="diag" uid="1035">
                <nodeList></nodeList>
                <zPlaneList></zPlaneList>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
        </root>
        """
    )
    loops = extract_loops(root)
    assert len(loops) == 1
    assert "1030" in loops[0].boundary_terminal_uids

    init_tunnels = [t for t in loops[0].tunnels if t.tunnel_type == "initFeedback"]
    assert len(init_tunnels) == 1
    t = init_tunnels[0]
    assert isinstance(t, Tunnel)
    assert t.outer_terminal_uid == "1030"
    # A synthetic inner uid, never a real heap uid -- must not collide with
    # the real uid (1485/1486) the Feedback Node's own slave terminal owns.
    assert t.inner_terminal_uid != "1485"
    assert t.inner_terminal_uid != "1486"


def test_feedback_init_glyph_kind_mapping_and_color():
    """The scene maps tunnel_type "initFeedback" -> glyph_kind "feedback_init"
    and includes it in the WIRE-TYPE-COLOR allow-list (easy to forget when
    adding a new border-terminal kind -- the color allow-list is a separate
    gate from the kind mapping, and silently falls back to no color)."""
    from lvkit.render.scene import _TUNNEL_GLYPH_KIND

    assert _TUNNEL_GLYPH_KIND["initFeedback"] == "feedback_init"


def test_feedback_init_terminal_glyph_draws_diamond_on_light_box():
    """Verified against the issue's own reference screenshot: a light
    (cream/canvas) box with a type-colored OUTLINE, and a small FILLED
    DIAMOND in that same color centered inside -- dark-on-light, matching
    the Feedback Node's own unwired-initializer asterisk marker's
    convention. NOT a solid type-colored fill with a light dot -- that was
    an earlier, wrong reading from a paraphrased text description instead
    of the actual reference image."""
    from lvkit.render.backend import SvgBackend
    from lvkit.render.glyphs.terminals.factory import border_terminal_glyph
    from lvkit.render.style import DEFAULT_THEME

    glyph = border_terminal_glyph("feedback_init", color="#4a9c3e")
    backend = SvgBackend()
    glyph.draw(backend, (0.0, 0.0, 16.0, 16.0), DEFAULT_THEME)
    svg = backend.render((0.0, 0.0, 16.0, 16.0))
    assert "<polygon" in svg
    assert f'fill="{DEFAULT_THEME.loop_term_fill}"' in svg
    assert 'stroke="#4a9c3e"' in svg


def test_ordinary_shift_register_unaffected():
    """Sanity: a REAL lSR/rSR pair still goes through the normal tunnel
    path unchanged -- the new initFeedback branch is additive."""
    root = ET.fromstring(
        """
        <root>
          <SL__arrayElement class="whileLoop" uid="700">
            <termList>
              <SL__arrayElement class="term" uid="710">
                <dco class="lSR" uid="711">
                  <typeDesc>TypeID(1)</typeDesc>
                  <termList>
                    <SL__arrayElement uid="720" />
                    <SL__arrayElement uid="710" />
                    </termList>
                  </dco>
                </SL__arrayElement>
              </termList>
            <diagramList>
              <SL__arrayElement class="diag" uid="705">
                <nodeList></nodeList>
                <zPlaneList></zPlaneList>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
        </root>
        """
    )
    loops = extract_loops(root)
    assert len(loops) == 1
    sr_tunnels = [t for t in loops[0].tunnels if t.tunnel_type == "lSR"]
    assert len(sr_tunnels) == 1
    assert sr_tunnels[0].outer_terminal_uid == "710"
    assert sr_tunnels[0].inner_terminal_uid == "720"
