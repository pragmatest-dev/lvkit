"""Render tests for the front-panel SVG (render/front_panel/).

Hermetic tests build ``ParsedFrontPanel``/``ParsedFPControl`` fixtures by
hand (no corpus needed) -- mirroring test_connector_pane_render.py's
convention. The ``needs_samples`` tests prove it end to end on real corpus
files, including the .ctl from GitHub issue #101 that motivated this
renderer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.parser.layout import ClusterFieldGeom, ClusterGeom
from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.geometry import build_boxes, content_bounds
from lvkit.render.style import DEFAULT_THEME


def _control(name: str, control_type: str, bounds, **kw) -> ParsedFPControl:
    return ParsedFPControl(uid=name, name=name, control_type=control_type,
                            bounds=bounds, **kw)


def test_renders_svg_for_flat_controls():
    fp = ParsedFrontPanel(
        controls=[
            _control("On", "stdBool", (0, 0, 20, 60), default_value="True"),
            _control("Count", "stdNum", (0, 70, 20, 150), default_value="42"),
        ],
        panel_bounds=(0, 0, 100, 200),
    )
    svg = render_front_panel_svg(fp)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "42" in svg


def test_unsupported_control_type_falls_back_to_labeled_box():
    """A control type outside week-1 scope never disappears silently --
    it draws as a dashed labeled box naming its real control_type."""
    fp = ParsedFrontPanel(
        controls=[_control("VI Ref", "stdRefNum", (0, 0, 40, 80))],
        panel_bounds=(0, 0, 100, 100),
    )
    svg = render_front_panel_svg(fp)
    assert "stdRefNum" in svg
    assert "stroke-dasharray" in svg


def test_nested_cluster_children_placed_inside_parent_bounds():
    """A cluster's children (from cluster_geom) land INSIDE the parent's own
    real box -- the correctness the origin-transform work exists for."""
    geom = ClusterGeom(
        width=100.0, height=100.0,
        fields=(
            ClusterFieldGeom(name="A", value_rect=(0.0, 0.0, 40.0, 100.0),
                              label_rect=None),
            ClusterFieldGeom(name="B", value_rect=(50.0, 0.0, 100.0, 100.0),
                              label_rect=None),
        ),
    )
    field_a = _control("A", "stdNum", (0, 0, 100, 40))
    field_b = _control("B", "stdNum", (0, 50, 100, 100))
    cluster = _control(
        "Group", "stdClust", (0, 0, 200, 200),
        children=[field_a, field_b], cluster_geom=geom,
    )
    fp = ParsedFrontPanel(controls=[cluster], panel_bounds=(0, 0, 300, 300))
    boxes = build_boxes(fp)
    root = boxes[0]
    assert len(root.children) == 2
    box_a, box_b = root.children
    # B's box is strictly to the right of A's -- real relative order preserved.
    assert box_b.bounds[0] >= box_a.bounds[2]
    # Both children stay within the parent's own placed box.
    for child in root.children:
        assert child.bounds[0] >= root.bounds[0]
        assert child.bounds[2] <= root.bounds[2]


def test_content_bounds_ignores_panel_bounds_when_smaller_than_controls():
    """The SVG viewBox is the real control extent, NOT
    ParsedFrontPanel.panel_bounds -- verified on a real corpus .ctl (issue
    #101) whose recorded panel_bounds was SMALLER than its root control's
    own bounds (LabVIEW's front-panel WINDOW size can be scrolled smaller
    than the actual content)."""
    fp = ParsedFrontPanel(
        controls=[_control("Big", "stdClust", (0, 0, 500, 500))],
        panel_bounds=(0, 0, 50, 50),  # deliberately smaller than the control
    )
    boxes = build_boxes(fp)
    bounds = content_bounds(boxes)
    assert bounds[2] > 50  # x2 extends past the tiny panel_bounds


def test_svg_escapes_special_chars_in_names():
    fp = ParsedFrontPanel(
        controls=[_control('A<B>&"C"', "stdNum", (0, 0, 20, 60))],
        panel_bounds=(0, 0, 100, 100),
    )
    svg = render_front_panel_svg(fp)
    assert "<B>" not in svg  # raw angle bracket would break the SVG's own XML


_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_TEST_SETTINGS_CTL = (
    _SAMPLES / "ni-labview-icon-editor" / "vi.lib" / "LabVIEW Icon API"
    / "API_Test Settings.ctl"
)


@pytest.mark.needs_samples
@pytest.mark.skipif(
    not _TEST_SETTINGS_CTL.exists(), reason="ni-labview-icon-editor sample absent"
)
def test_real_ctl_renders_without_crashing():
    """The full pipeline (load_typedef -> render_ctl_front_panel) on a real
    corpus .ctl with genuine nested-cluster structure."""
    from lvkit.graph.core import InMemoryVIGraph
    from lvkit.render.front_panel import render_ctl_front_panel

    g = InMemoryVIGraph()
    key = g.load_typedef(str(_TEST_SETTINGS_CTL))
    svg = render_ctl_front_panel(g, key)
    assert svg is not None
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert "Font" in svg and "Text color" in svg


_LIST_VI_HIERARCHY = (
    _SAMPLES / "OpenG" / "extracted" / "File Group 0" / "user.lib" / "_OpenG.lib"
    / "appcontrol" / "appcontrol.llb" / "List VI Hierarchy__ogtk.vi"
)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _LIST_VI_HIERARCHY.exists(), reason="OpenG sample absent")
def test_real_vi_front_panel_renders_without_crashing():
    """The full pipeline (load_vi_by_path -> render_vi_front_panel) on a
    real corpus VI."""
    from lvkit.graph import load_vi_by_path
    from lvkit.graph.loading import LoadMode
    from lvkit.render.front_panel import render_vi_front_panel

    g, name = load_vi_by_path(str(_LIST_VI_HIERARCHY), LoadMode.NONE)
    svg = render_vi_front_panel(g, name)
    assert svg is not None
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")


def test_default_theme_colors_present():
    fp = ParsedFrontPanel(
        controls=[_control("On", "stdBool", (0, 0, 20, 60), default_value="True")],
        panel_bounds=(0, 0, 100, 100),
    )
    svg = render_front_panel_svg(fp)
    assert DEFAULT_THEME.wire_bool in svg
