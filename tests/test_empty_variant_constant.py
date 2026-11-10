"""The empty Variant constant is ``None`` in generated code and ``Variant()`` in
the views."""

from __future__ import annotations

from tests.error_diagrams import (
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Empty.vi"
_VARIANT = '<TypeDesc Type="Variant" Format="inline" />'
EMPTY_VARIANT = "0900800000000001000400000001000000000000"
# A Variant with no value but one attribute: not the empty Variant.
_ONE_ATTRIBUTE = (
    "0900800000000001000400000001000000000001"
    "00000001" + "61" + EMPTY_VARIANT
)


def _vi(hex_value: str) -> Vi:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    vi.wire(vi.constant(variant_type, hex_value), vi.indicator("out", variant_type))
    return vi


def test_the_empty_variant_constant_is_none(project: Project) -> None:  # noqa: F811
    project.add(_vi(EMPTY_VARIANT))
    package = project.generate(NAME)
    assert "'Variant()'" not in package.source
    assert package.function().out is None


def test_the_views_still_show_variant(project: Project) -> None:  # noqa: F811
    project.add(_vi(EMPTY_VARIANT))
    views = project.views(NAME)
    assert "Variant()" in views.describe
    assert "Variant()" in views.lvnet


def test_a_variant_with_an_attribute_is_not_none(project: Project) -> None:  # noqa: F811
    project.add(_vi(_ONE_ATTRIBUTE))
    views = project.views(NAME)
    assert "attributes=" in views.lvnet
