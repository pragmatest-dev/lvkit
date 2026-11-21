"""A typed wire into a subVI input the called VI declares as a Variant (a call to an
adapt-to-type input records the caller's own type on the call terminal) reaches the
called VI as a ``Variant``, so the called VI reads its ``.type`` and ``.name``."""

from __future__ import annotations

from tests.error_diagrams import (
    I32,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Caller.vi"
_VARIANT = '<TypeDesc Type="Variant" Format="inline" />'


def _passthrough(name: str) -> Vi:
    """``name(variant) -> table`` wiring a Variant control to a Variant indicator."""
    vi = Vi(name)
    variant_type = vi.add_type(_VARIANT)
    variant = vi.control("variant", variant_type, REQUIRED)
    vi.wire(variant, vi.indicator("table", variant_type))
    return vi


def _caller(callee: str) -> Vi:
    """``Caller(x: I32) -> table``: ``x`` wired to the called VI's Variant input,
    the call recording the input as an I32."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    call = vi.subvi(callee, variant=(0, I32, False), table=(1, variant_type, True))
    vi.wire(vi.control("x", I32, REQUIRED), call["variant"])
    vi.wire(call["table"], vi.indicator("table", variant_type))
    return vi


def test_a_typed_wire_into_a_variant_input_of_the_called_vi_is_made_a_variant(
    project: Project,  # noqa: F811
) -> None:
    project.add(_passthrough("Pass.vi"))
    project.add(_caller("Pass.vi"))
    package = project.generate(NAME)
    result = package.function(5)
    assert result.table == package.variants.Variant(5, package.variants.LV_I32)
