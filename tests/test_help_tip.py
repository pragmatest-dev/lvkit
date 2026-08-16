"""Regression guards for the viewer-host connector-panel hover (``help_tip``).

These pin two fixes that are pure client-side JS (so they'd otherwise only be
caught by eyeballing a browser): the panel must render at a CONSTANT on-screen
size regardless of diagram zoom (Bug 2 / task #86), and it must be clamped to
the diagram VIEW AREA so it can't bleed onto the diff viewer's changes list.
"""

from __future__ import annotations

from lvkit.render.help_tip import HELP_TIP


def test_hover_panel_is_readable_capped_to_view_and_constant_across_zoom():
    """Bug 2 (#86) + the side-by-side overflow complaint: the connector panel is
    sized to a READABLE constant (``SCALE`` css px per user unit) and CAPPED so
    it can never exceed the visible view — max = a fraction of the view
    container's box, min = the readable target. Both inputs are zoom-independent
    (``SCALE`` is constant; the view box doesn't change as you zoom inside it),
    so the panel is the SAME SIZE at every diagram-zoom level while never
    overflowing.
    """
    # readable target = a constant px-per-user-unit (NOT the diagram's zoom)
    assert "var scale = SCALE" in HELP_TIP
    # ...capped to the VIEW container's box so it can never overflow
    assert "Math.min" in HELP_TIP
    assert 'closest(".stage-wrap")' in HELP_TIP
    assert "vr.width" in HELP_TIP and "vr.height" in HELP_TIP
    # final size is the bbox times the clamped scale
    assert "bb.width * scale" in HELP_TIP and "bb.height * scale" in HELP_TIP
    # NEVER sized from the SVG's own rendered size or a display-scale ratio,
    # which grow with diagram zoom (the ballooning regression)
    assert "root.getBoundingClientRect" not in HELP_TIP
    assert "viewBox.baseVal.width" not in HELP_TIP


def test_hover_panel_clamps_to_the_diagram_view_area_not_the_window():
    """The panel is clamped to the ``.stage-wrap`` view area (the element that
    clips/scrolls the SVG), so in the diff viewer it stays over the diagram and
    never drifts onto the changes list — not to the raw window viewport."""
    assert "viewRect" in HELP_TIP
    assert ".stage-wrap" in HELP_TIP
    # position() clamps against the view rect's edges, not bare innerWidth/Height
    assert "vr.right" in HELP_TIP and "vr.left" in HELP_TIP
    assert "vr.bottom" in HELP_TIP and "vr.top" in HELP_TIP
