"""``stdLvVariant`` front-panel controls: opaque, type-erased data -- there is
no internal structure to show (a Variant's underlying_type is always just
``"Variant"``/``"LVVariant"``, unlike a refnum's distinguishing ``ref_type``),
so the SAME solid-box ``VariantGlyph`` a block-diagram Variant wire draws is
the honest front-panel form too, just with the neutral front-panel border
every other value cell uses instead of the wire-purple a diagram uses."""

from __future__ import annotations

import pytest

from lvkit.parser.models import ParsedFrontPanel
from lvkit.parser.vi import parse_vi
from lvkit.render.front_panel import render_front_panel_svg
from lvkit.render.front_panel.controls.leaf import leaf_glyph
from lvkit.render.front_panel.controls.resolve import resolve_glyph
from lvkit.render.front_panel.controls.unknown import UnknownControlGlyph
from lvkit.render.glyph import ClusterConstantGlyph, VariantGlyph
from lvkit.render.style import DEFAULT_THEME

from .conftest import SAMPLES_ROOT
from .test_front_panel_render import _control, _rects_filled

_PACK_FOR_GRPC_VI = (
    SAMPLES_ROOT
    / "measurement-plugin-labview/Source/Tests/Tests.Runtime/Measurement Server"
    / "Acceptance/Measurement Service Tests/Continuous Counting Measurement"
    / "Pack for gRPC.vi"
)


def test_leaf_glyph_draws_a_variant_as_a_neutral_bordered_box() -> None:
    glyph = leaf_glyph("stdLvVariant", None, [], DEFAULT_THEME)
    assert isinstance(glyph, VariantGlyph)
    assert glyph.stroke_attr == "struct_border"  # neutral, not the wire color
    assert glyph.fill_attr != "wire_variant"  # not the diagram's purple fill


def test_a_variant_control_actually_draws_with_the_right_theme_attrs() -> None:
    """Hermetic, end-to-end through the real SVG markup: ``VariantGlyph.draw()``
    resolves ``fill_attr``/``stroke_attr`` via ``getattr(theme, ...)``, so a
    typo'd attribute name would only surface as a runtime ``AttributeError``
    on a real render -- this exercises that call, not just the glyph's own
    field values."""
    fp = ParsedFrontPanel(
        controls=[_control("V", "stdLvVariant", (0, 0, 40, 80))],
        panel_bounds=(0, 0, 100, 100),
    )
    svg = render_front_panel_svg(fp)
    assert _rects_filled(
        svg, DEFAULT_THEME.fp_value_fill, stroke=DEFAULT_THEME.struct_border
    )


def test_a_variant_cluster_field_resolves_through_the_same_generic_path() -> None:
    """A Variant nested in a cluster field falls through the SAME generic
    ``leaf_glyph`` dispatch a top-level Variant does -- no Variant-specific
    cluster handling exists or is needed."""
    field = _control("V", "stdLvVariant", (0, 0, 40, 40))
    cluster = _control("State", "stdClust", (0, 0, 50, 50), children=[field])
    glyph = resolve_glyph(cluster, DEFAULT_THEME)
    assert isinstance(glyph, ClusterConstantGlyph)
    (_, field_glyph) = glyph.fields[0]
    assert isinstance(field_glyph, VariantGlyph)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _PACK_FOR_GRPC_VI.exists(), reason="sample absent")
def test_a_real_corpus_variant_control_renders_not_unknown() -> None:
    """End-to-end against a real VI with a ``stdLvVariant`` control: it
    resolves to ``VariantGlyph`` and renders, no crash, never
    ``UnknownControlGlyph``."""
    parsed = parse_vi(_PACK_FOR_GRPC_VI)

    def walk(ctrls):  # noqa: ANN001, ANN202
        for c in ctrls:
            yield c
            yield from walk(c.children)

    variant = next(
        c for c in walk(parsed.front_panel.controls) if c.control_type == "stdLvVariant"
    )
    glyph = resolve_glyph(variant, DEFAULT_THEME)
    assert isinstance(glyph, VariantGlyph)
    assert not isinstance(glyph, UnknownControlGlyph)
    svg = render_front_panel_svg(parsed.front_panel, title=_PACK_FOR_GRPC_VI.name)
    assert "<svg" in svg
