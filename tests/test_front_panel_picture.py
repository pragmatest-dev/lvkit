"""``stdPict`` (Picture) front-panel controls: LabVIEW's own saved value is
NOT a raster -- checked against real corpus bytes (no PNG/BMP magic number, a
small-integer/length-prefixed byte pattern instead, consistent with a
drawing-command opcode stream, though the specific opcodes were not decoded).
Decoding it is out of scope, so this draws a generic placeholder icon rather
than fake content."""

from __future__ import annotations

import pytest

from lvkit.parser.models import ParsedFrontPanel
from lvkit.parser.vi import parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.picture import PictureControlGlyph
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.front_panel.controls.unknown import UnknownControlGlyph
from lvkit.render.glyph import ClusterConstantGlyph
from lvkit.render.style import DEFAULT_THEME

from .conftest import SAMPLES_ROOT
from .test_front_panel_render import _control, _rects_filled

_NEW_PICTURE_VI = (
    SAMPLES_ROOT / "ni-labview-icon-editor/resource/plugins/NIIconEditor/Support"
    / "Create or Substitute NI_Layer layer.vi"
)


def test_a_picture_control_draws_a_bordered_placeholder() -> None:
    fp = ParsedFrontPanel(
        controls=[_control("Image", "stdPict", (0, 0, 60, 60))],
        panel_bounds=(0, 0, 100, 100),
    )
    svg = render_front_panel_svg(fp)
    assert _rects_filled(
        svg, DEFAULT_THEME.fp_value_fill, stroke=DEFAULT_THEME.struct_border
    )


def test_a_picture_cluster_field_resolves_through_the_same_generic_path() -> None:
    """(No real corpus example of a stdPict nested in an array exists either
    -- the same generic ``_element`` dispatch would apply there too.)"""
    field = _control("Pic", "stdPict", (0, 0, 40, 40))
    cluster = _control("State", "stdClust", (0, 0, 50, 50), children=[field])
    glyph = resolve_glyph(cluster, DEFAULT_THEME)
    assert isinstance(glyph, ClusterConstantGlyph)
    (_, field_glyph) = glyph.fields[0]
    assert isinstance(field_glyph, PictureControlGlyph)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _NEW_PICTURE_VI.exists(), reason="icon-editor sample absent")
def test_a_real_corpus_picture_control_renders_not_unknown() -> None:
    parsed = parse_vi(_NEW_PICTURE_VI)
    picture = next(
        c for c in parsed.front_panel.controls if c.control_type == "stdPict"
    )
    glyph = resolve_glyph(picture, DEFAULT_THEME)
    assert isinstance(glyph, PictureControlGlyph)
    assert not isinstance(glyph, UnknownControlGlyph)
    svg = render_front_panel_svg(parsed.front_panel, title=_NEW_PICTURE_VI.name)
    assert "<svg" in svg
