"""Regression (#107): an sRN (shift-register node)'s OWN ``<bounds>`` is a
translation for out-of-diagram terminal REFERENCES, not a real page position
(see ``_visit``'s own comment in ``parser/layout.py`` -- it already avoids
using this box as the origin for the sRN's children). It must also stay OUT
of ``Layout.node_bounds`` -- the union ``Layout.scene_bounds()`` takes for the
whole diagram's viewBox -- or a LabVIEW-internal placeholder value unrelated
to anything actually drawn silently balloons the page by tens of thousands of
units (a real corpus VI's scene went from ~4000x1300 to ~27000x13500 from
exactly one such sRN).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from lvkit.parser.layout import build_layout_from_root


def test_srn_own_bounds_excluded_from_node_bounds():
    # A degenerate, far-flung sRN bounds (LabVIEW's real convention for this
    # tag -- see #107) alongside an ordinary, real node with sane bounds.
    root = ET.fromstring(
        """
        <root>
          <nodeList>
            <SL__arrayElement class="sRN" uid="996">
              <bounds>(-25250, -12378, -25250, -12378)</bounds>
              <termList></termList>
            </SL__arrayElement>
          </nodeList>
          <zPlaneList>
            <SL__arrayElement class="prim" uid="p1">
              <bounds>(0, 0, 20, 10)</bounds>
              <termList></termList>
            </SL__arrayElement>
          </zPlaneList>
        </root>
        """
    )
    layout = build_layout_from_root(root)
    assert "996" not in layout.node_bounds
    assert "p1" in layout.node_bounds
    # Padded around the one real node (p1) -- nowhere near the sRN's
    # degenerate (-25250, -12378) corner, which would blow this out to a
    # ~25000-unit-wide scene if it weren't excluded.
    x1, y1, x2, y2 = layout.scene_bounds()
    assert x2 - x1 < 200
    assert y2 - y1 < 200


def test_feedback_node_master_terminal_bounds_stay_inside_node_box():
    """Regression (#107): a Feedback Node MASTER's (``hiddenFBNode``) own
    ``leftFeedback``/``initFeedback`` dco ``<termBounds>`` are ALREADY
    diagram-relative -- the exact same quirk as an sRN's termList (see the
    module docstring above) -- verified on two real instances in a real
    corpus VI (FPGA_v1.vi): a terminal's ``<termBounds>`` sit in the SAME
    numeric range as the node's own ``<bounds>``, not a 0-based offset within
    it like an ordinary primitive's termBounds.

    Before the fix, ``_visit`` added the node's own absolute corner on top
    of that already-diagram-relative termBounds, double-counting the node's
    local offset and placing the terminal (and the glyph's terminal-span
    box, ``draw._glyph_bounds``) hundreds of units away -- outside its own
    structure's clip region and invisible, even though the node's OWN
    ``<bounds>`` (and its sibling nodes) landed correctly."""
    root = ET.fromstring(
        """
        <root>
          <zPlaneList>
            <SL__arrayElement class="forLoop" uid="loop1">
              <bounds>(25, 37, 191, 392)</bounds>
              <diagramList>
                <SL__arrayElement class="diag" uid="d1">
                  <zPlaneList>
                    <SL__arrayElement class="hiddenFBNode" uid="fb1">
                      <bounds>(133, 230, 157, 262)</bounds>
                      <termList>
                        <SL__arrayElement class="term" uid="t1">
                          <dco class="leftFeedback" uid="dco1">
                            <termBounds>(133, 230, 145, 246)</termBounds>
                          </dco>
                        </SL__arrayElement>
                      </termList>
                    </SL__arrayElement>
                  </zPlaneList>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
          </zPlaneList>
        </root>
        """
    )
    layout = build_layout_from_root(root)
    node_box = layout.node_bounds["fb1"]
    term_box = layout.node_bounds["t1"]
    # The master's own terminal must land WITHIN its node's own box -- not
    # hundreds of units away from double-counting the local offset.
    nx1, ny1, nx2, ny2 = node_box
    tx1, ty1, tx2, ty2 = term_box
    assert nx1 - 1 <= tx1 <= tx2 <= nx2 + 1
    assert ny1 - 1 <= ty1 <= ty2 <= ny2 + 1


def test_feedback_node_slave_terminal_unaffected():
    """The SLAVE's (``slaveFBInputNode``) own ``rightFeedback`` termBounds do
    NOT have the master's quirk -- they're genuinely node-relative, like any
    other primitive's -- so the sRN-style exemption must stay scoped to
    ``hiddenFBNode`` only; applying it to the slave too would double-REMOVE
    its offset and break it the other way."""
    root = ET.fromstring(
        """
        <root>
          <zPlaneList>
            <SL__arrayElement class="forLoop" uid="loop1">
              <bounds>(25, 37, 191, 392)</bounds>
              <diagramList>
                <SL__arrayElement class="diag" uid="d1">
                  <zPlaneList>
                    <SL__arrayElement class="slaveFBInputNode" uid="fb1s">
                      <bounds>(133, 230, 157, 262)</bounds>
                      <termList>
                        <SL__arrayElement class="term" uid="t2">
                          <dco class="rightFeedback" uid="dco2">
                            <termBounds>(0, 16, 12, 32)</termBounds>
                          </dco>
                        </SL__arrayElement>
                      </termList>
                    </SL__arrayElement>
                  </zPlaneList>
                </SL__arrayElement>
              </diagramList>
            </SL__arrayElement>
          </zPlaneList>
        </root>
        """
    )
    layout = build_layout_from_root(root)
    node_box = layout.node_bounds["fb1s"]
    term_box = layout.node_bounds["t2"]
    nx1, ny1, nx2, ny2 = node_box
    tx1, ty1, tx2, ty2 = term_box
    assert nx1 - 1 <= tx1 <= tx2 <= nx2 + 1
    assert ny1 - 1 <= ty1 <= ty2 <= ny2 + 1


def test_non_srn_own_bounds_still_recorded():
    # Sanity: the exclusion is specific to class="sRN", not a blanket
    # nodeList-vs-zPlaneList distinction -- a decompose-structure border tab
    # (also found in nodeList) keeps its own bounds.
    root = ET.fromstring(
        """
        <root>
          <nodeList>
            <SL__arrayElement class="decomposeClusterNode" uid="d1">
              <bounds>(0, 0, 15, 8)</bounds>
              <termList></termList>
            </SL__arrayElement>
          </nodeList>
        </root>
        """
    )
    layout = build_layout_from_root(root)
    assert "d1" in layout.node_bounds
