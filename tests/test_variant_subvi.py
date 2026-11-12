"""Get Variant Attribute on a ``Variant`` a subVI produces or passes on: the
attributes travel with the ``Variant`` object through every call, whatever the
caller wired."""

from __future__ import annotations

from tests.error_diagrams import (
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)
from tests.test_variant_lookup import (
    EMPTY_VARIANT,
    _get_pane,
    _set_pane,
)

NAME = "Caller.vi"
_VARIANT = '<TypeDesc Type="Variant" Format="inline" />'


def _builder(name: str) -> Vi:
    """``name(key, content) -> table``: Set Variant Attribute on an empty Variant
    constant."""
    vi = Vi(name)
    variant_type = vi.add_type(_VARIANT)
    key = vi.control("key", STR, REQUIRED)
    content = vi.control("content", STR, REQUIRED)
    table = vi.indicator("table", variant_type)
    node = vi.primitive(8204, _set_pane(variant_type))
    vi.wire(vi.constant(variant_type, EMPTY_VARIANT), node["variant"])
    vi.wire(key, node["name"])
    vi.wire(content, node["value"])
    vi.wire(node["variant out"], table)
    return vi


def _wrapper(name: str, inner: str) -> Vi:
    """``name(key, content) -> table`` that calls the builder ``inner``."""
    vi = Vi(name)
    variant_type = vi.add_type(_VARIANT)
    key = vi.control("key", STR, REQUIRED)
    content = vi.control("content", STR, REQUIRED)
    table = vi.indicator("table", variant_type)
    call = vi.subvi(
        inner,
        key=(0, STR, False),
        content=(1, STR, False),
        table=(2, variant_type, True),
    )
    vi.wire(key, call["key"])
    vi.wire(content, call["content"])
    vi.wire(call["table"], table)
    return vi


def _passthrough(name: str) -> Vi:
    """``name(variant) -> table`` wiring its control straight to its indicator."""
    vi = Vi(name)
    variant_type = vi.add_type(_VARIANT)
    variant = vi.control("variant", variant_type, REQUIRED)
    vi.wire(variant, vi.indicator("table", variant_type))
    return vi


def _get_from(vi: Vi, variant_type: int, table: int) -> None:
    node = vi.primitive(8205, _get_pane(variant_type))
    vi.wire(table, node["variant"])
    vi.wire(vi.control("lookup", STR, REQUIRED), node["name"])
    vi.wire(node["value"], vi.indicator("value", variant_type))
    vi.wire(node["found"], vi.indicator("found", variant_type))


def _caller_of(callee: str) -> Vi:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    key = vi.control("key", STR, REQUIRED)
    content = vi.control("content", STR, REQUIRED)
    call = vi.subvi(
        callee,
        key=(0, STR, False),
        content=(1, STR, False),
        table=(2, variant_type, True),
    )
    vi.wire(key, call["key"])
    vi.wire(content, call["content"])
    _get_from(vi, variant_type, call["table"])
    return vi


def _table(package, **attributes):
    variants = package.variants
    return variants.Variant(
        attributes={
            k: variants.Variant(v, variants.String) for k, v in attributes.items()
        }
    )


def test_a_subvi_built_table_carries_its_attributes(
    project: Project,  # noqa: F811
) -> None:
    project.add(_builder("Build.vi"))
    project.add(_caller_of("Build.vi"))
    package = project.generate(NAME)
    result = package.function("colour", "red", "colour")
    assert (result.value, result.found) == (
        package.variants.Variant("red", package.variants.String),
        True,
    )


def test_a_table_built_two_subvis_down_carries_its_attributes(
    project: Project,  # noqa: F811
) -> None:
    project.add(_builder("Build.vi"))
    project.add(_wrapper("Wrap.vi", "Build.vi"))
    project.add(_caller_of("Wrap.vi"))
    package = project.generate(NAME)
    result = package.function("colour", "red", "colour")
    assert (result.value, result.found) == (
        package.variants.Variant("red", package.variants.String),
        True,
    )


def _passes_variant(callee: str, *, wired: str, pane: bool = False) -> Vi:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    call = vi.subvi(
        callee, variant=(0, variant_type, False), table=(1, variant_type, True)
    )
    if wired == "empty":
        node = vi.primitive(8204, _set_pane(variant_type))
        vi.wire(vi.constant(variant_type, EMPTY_VARIANT), node["variant"])
        vi.wire(vi.control("key", STR, REQUIRED), node["name"])
        vi.wire(vi.control("content", STR, REQUIRED), node["value"])
        vi.wire(node["variant out"], call["variant"])
    else:
        vi.wire(
            vi.control("given", variant_type, REQUIRED if pane else None),
            call["variant"],
        )
    _get_from(vi, variant_type, call["table"])
    return vi


def test_a_passthrough_of_an_empty_built_variant_carries_its_attributes(
    project: Project,  # noqa: F811
) -> None:
    project.add(_passthrough("Pass.vi"))
    project.add(_passes_variant("Pass.vi", wired="empty"))
    package = project.generate(NAME)
    result = package.function("colour", "red", "colour")
    assert (result.value, result.found) == (
        package.variants.Variant("red", package.variants.String),
        True,
    )


def test_a_passthrough_of_a_parameter_of_the_top_vi_returns_the_variant_passed(
    project: Project,  # noqa: F811
) -> None:
    project.add(_passthrough("Pass.vi"))
    project.add(_passes_variant("Pass.vi", wired="control", pane=True))
    package = project.generate(NAME)
    result = package.function(_table(package, colour="red"), "colour")
    assert result.value == package.variants.Variant("red", package.variants.String)


def test_a_passthrough_of_a_control_off_the_pane_passes_its_empty_default(
    project: Project,  # noqa: F811
) -> None:
    project.add(_passthrough("Pass.vi"))
    project.add(_passes_variant("Pass.vi", wired="control"))
    package = project.generate(NAME)
    assert package.gaps == []
    result = package.function("a")
    assert (result.value, result.found) == (package.variants.Variant(), False)
