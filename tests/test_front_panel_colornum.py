"""``stdColorNum`` (Color Box) front-panel controls: the saved value is a
plain ``NumUInt32`` (its own recorded RGB, packed the SAME ``00RRGGBB`` way
``parser.utils.heap_color`` already reads for a heap part's ``fgColor``/
``bgColor``) -- decoded by the EXISTING generic numeric decode path, no
parser change needed. The render view draws a solid swatch of that color."""

from __future__ import annotations

import pytest

from lvkit.parser.models import ParsedFrontPanel
from lvkit.parser.vi import _decode_default_data, parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.color import ColorControlGlyph
from lvkit.render.front_panel.controls.leaf import leaf_glyph
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.front_panel.controls.unknown import UnknownControlGlyph
from lvkit.render.glyph import ClusterConstantGlyph
from lvkit.render.style import DEFAULT_THEME

from .conftest import SAMPLES_ROOT
from .test_front_panel_render import _control, _rects_filled

_ICON_API = SAMPLES_ROOT / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API"
_MAGIC_TRANSPARENT_VI = (
    _ICON_API / "lv_icon/Support/Magic Transparent Color Constant.vi"
)
_IE_COLOR_CTL = _ICON_API / "lv_icon/Controls/IEColor.ctl"


def _color(default_value: str | None) -> str | None:
    glyph = leaf_glyph("stdColorNum", default_value, [], DEFAULT_THEME)
    assert isinstance(glyph, ColorControlGlyph)
    return glyph.color


def test_leaf_glyph_decodes_the_saved_rgb_value() -> None:
    """A near-white value (``0x00FFFFFE``, LabVIEW's "magic transparent"
    color) and a green one (``0x004AFF12``), decoded the exact same way
    real corpus bytes for each were confirmed to decode by hand."""
    assert _color("16777214") == "#FFFFFE"
    assert _color("4914962") == "#4AFF12"


def test_the_parser_decodes_a_color_default_even_with_no_resolvable_vctp() -> None:
    """The unresolved-VCTP fallback (``lv_type=None``) must handle
    ``stdColorNum`` the same as ``stdNum``/``stdNumeric`` -- same 4-byte
    shape, decoded by the same ``_decode_numeric_default``."""
    raw = "&#x00;&#xff;&#xff;&#xfe;"  # 0x00FFFFFE, the real "magic transparent" bytes
    decoded, _structured = _decode_default_data(raw, "stdColorNum", None)
    assert decoded == "16777214"


def test_leaf_glyph_draws_a_bordered_box_when_the_value_never_decoded() -> None:
    """No saved default -- still a bordered box, with the SAME recessed-cell
    fill every other unset value uses (never ``UnknownControlGlyph``, and
    never ``theme.canvas``'s "outside scope" convention), since
    ``control_type`` alone already identifies the shape."""
    glyph = leaf_glyph("stdColorNum", None, [], DEFAULT_THEME)
    assert isinstance(glyph, ColorControlGlyph)
    assert glyph.color is None
    fp = ParsedFrontPanel(
        controls=[_control("V", "stdColorNum", (0, 0, 40, 40))],
        panel_bounds=(0, 0, 100, 100),
    )
    svg = render_front_panel_svg(fp)
    assert _rects_filled(
        svg, DEFAULT_THEME.fp_value_fill, stroke=DEFAULT_THEME.struct_border
    )


def test_a_value_outside_the_00rrggbb_packing_never_decodes() -> None:
    """A value above 0xFFFFFF (the packing's own high-byte-must-be-zero
    convention -- see ``heap_color``) is not a color to draw with."""
    assert _color("16777216") is None
    assert _color("not a number") is None


def test_a_color_swatch_actually_draws_with_its_own_color() -> None:
    """Hermetic, end-to-end through the real SVG markup."""
    fp = ParsedFrontPanel(
        controls=[
            _control("V", "stdColorNum", (0, 0, 40, 40), default_value="16711680")
        ],
        panel_bounds=(0, 0, 100, 100),
    )
    svg = render_front_panel_svg(fp)
    assert _rects_filled(svg, "#FF0000", stroke=DEFAULT_THEME.struct_border)


def test_a_color_cluster_field_resolves_through_the_same_generic_path() -> None:
    """A Color Box nested in a cluster field falls through the SAME generic
    ``leaf_glyph`` dispatch a top-level one does -- no color-specific
    cluster handling exists or is needed (real corpus data has stdColorNum
    nested both under ``stdClust`` and ``indArr``)."""
    field = _control("Fill", "stdColorNum", (0, 0, 40, 40), default_value="65280")
    cluster = _control("Style", "stdClust", (0, 0, 50, 50), children=[field])
    glyph = resolve_glyph(cluster, DEFAULT_THEME)
    assert isinstance(glyph, ClusterConstantGlyph)
    (_, field_glyph) = glyph.fields[0]
    assert isinstance(field_glyph, ColorControlGlyph)
    assert field_glyph.color == "#00FF00"


@pytest.mark.needs_samples
@pytest.mark.skipif(
    not _MAGIC_TRANSPARENT_VI.exists(), reason="icon-editor sample absent"
)
def test_a_real_corpus_color_control_decodes_its_saved_value() -> None:
    """``Magic Transparent Color Constant.vi`` -- the real, named source of
    the ``0x00FFFFFE`` value used above -- decodes and renders end to end,
    never ``UnknownControlGlyph``."""
    parsed = parse_vi(_MAGIC_TRANSPARENT_VI)
    (ctrl,) = parsed.front_panel.controls
    assert ctrl.control_type == "stdColorNum" and ctrl.default_value == "16777214"
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    assert isinstance(glyph, ColorControlGlyph) and glyph.color == "#FFFFFE"
    assert not isinstance(glyph, UnknownControlGlyph)
    svg = render_front_panel_svg(parsed.front_panel, title=_MAGIC_TRANSPARENT_VI.name)
    assert "<svg" in svg


@pytest.mark.needs_samples
@pytest.mark.skipif(not _IE_COLOR_CTL.exists(), reason="icon-editor sample absent")
def test_a_real_corpus_ctl_color_control_decodes_its_saved_value() -> None:
    """A ``.ctl`` control front panel decodes the same way a VI's does."""
    parsed = parse_vi(_IE_COLOR_CTL)
    (ctrl,) = parsed.front_panel.controls
    assert ctrl.control_type == "stdColorNum" and ctrl.default_value == "4914962"
    glyph = resolve_glyph(ctrl, DEFAULT_THEME)
    assert isinstance(glyph, ColorControlGlyph) and glyph.color == "#4AFF12"
