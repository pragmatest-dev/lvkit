"""``stdGraph`` front-panel controls: a frame + the plot area's own real
inset (a distinct ``partID`` 28 rect, consistently inset from the outer
frame's ``partID`` 9 in real corpus heaps) -- never a fake trace, axis
numbers, legend, or cursor palette, since LabVIEW's own plot-data/scale heap
format is a much larger undertaking than a control's shape."""

from __future__ import annotations

import re

import pytest

from lvkit.parser.vi import parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.graph import GraphControlGlyph
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.front_panel.controls.unknown import UnknownControlGlyph
from lvkit.render.glyph import ClusterConstantGlyph
from lvkit.render.style import DEFAULT_THEME

from .conftest import SAMPLES_ROOT
from .test_front_panel_render import _control

_STIM_VI = SAMPLES_ROOT / "LabVIEW-DAQ/Fiber Photometry/GenerateStimulusOutputs_FP.vi"


def _rect(part_id: int, bounds) -> object:
    from lvkit.parser.models import ParsedFPPart

    return ParsedFPPart(part_id=part_id, part_class="cosm", bounds=bounds)


def test_a_graph_with_a_recorded_plot_area_insets_it_by_real_fraction() -> None:
    """Hermetic fixture (an invented rect, not a real corpus one -- see
    test_a_real_corpus_graph_control_renders_not_unknown for real numbers):
    the inset is computed from the control's own recorded plot-area rect
    (partID 28) as a fraction of its own heap box, never a guessed
    constant."""
    ctrl = _control(
        "Graph", "stdGraph", (0, 0, 100, 200),
        parts=[_rect(9, (0, 0, 100, 200)), _rect(28, (10, 20, 90, 180))],
    )
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    assert isinstance(glyph, GraphControlGlyph)
    left, top, right, bottom = glyph.inset
    assert left == pytest.approx(20 / 200)  # bounds is (top,left,bottom,right)
    assert top == pytest.approx(10 / 100)
    assert right == pytest.approx((200 - 180) / 200)
    assert bottom == pytest.approx((100 - 90) / 100)


def test_a_graph_with_no_recorded_plot_area_draws_just_the_frame() -> None:
    """An older/simpler graph variant whose heap carries no partID 28 --
    still a bordered box, never ``UnknownControlGlyph``, and no guessed
    inset."""
    ctrl = _control("Graph", "stdGraph", (0, 0, 100, 200))
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    assert isinstance(glyph, GraphControlGlyph)
    assert glyph.inset is None


def test_a_graph_actually_draws_its_inset_plot_area() -> None:
    """Hermetic, end-to-end through the real SVG markup: two nested
    ``<rect>``s, the inner one strictly inside the outer one."""
    ctrl = _control(
        "Graph", "stdGraph", (0, 0, 100, 100),
        parts=[_rect(9, (0, 0, 100, 100)), _rect(28, (10, 10, 90, 90))],
    )
    from lvkit.parser.models import ParsedFrontPanel

    fp = ParsedFrontPanel(controls=[ctrl], panel_bounds=(0, 0, 150, 150))
    svg = render_front_panel_svg(fp)
    rects = re.findall(
        r'<rect x="([\d.-]+)" y="([\d.-]+)" width="([\d.-]+)" height="([\d.-]+)"',
        svg,
    )
    graph_rects = [tuple(float(v) for v in r) for r in rects[-2:]]
    (ox, oy, ow, oh), (ix, iy, iw, ih) = graph_rects
    assert ix > ox and iy > oy and ix + iw < ox + ow and iy + ih < oy + oh


def test_a_graph_cluster_field_resolves_through_the_same_generic_path() -> None:
    field = _control("Graph", "stdGraph", (0, 0, 40, 40))
    cluster = _control("Config", "stdClust", (0, 0, 50, 50), children=[field])
    glyph = resolve_glyph(cluster, DEFAULT_THEME)
    assert isinstance(glyph, ClusterConstantGlyph)
    (_, field_glyph) = glyph.fields[0]
    assert isinstance(field_glyph, GraphControlGlyph)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _STIM_VI.exists(), reason="sample absent")
def test_a_real_corpus_graph_control_renders_not_unknown() -> None:
    """Real numbers from GenerateStimulusOutputs_FP.vi's own stdGraph heap
    (measured this session, not invented): its plot area (partID 28) sits at
    roughly 42% from the left, 12% from the top, 14% from the right and 17%
    from the bottom of the control's own box -- an off-center inset (unlike
    the symmetric hermetic fixture above), which is exactly the kind of real
    asymmetry a hand-picked fixture would not catch."""
    parsed = parse_vi(_STIM_VI)
    graph = next(c for c in parsed.front_panel.controls if c.control_type == "stdGraph")
    glyph = resolve_glyph(graph, DEFAULT_THEME)
    assert isinstance(glyph, GraphControlGlyph)
    assert not isinstance(glyph, UnknownControlGlyph)
    assert glyph.inset is not None
    left, top, right, bottom = glyph.inset
    assert left == pytest.approx(0.4167, abs=1e-3)
    assert top == pytest.approx(0.1181, abs=1e-3)
    assert right == pytest.approx(0.1369, abs=1e-3)
    assert bottom == pytest.approx(0.1730, abs=1e-3)
    svg = render_front_panel_svg(parsed.front_panel, title=_STIM_VI.name)
    assert "<svg" in svg
