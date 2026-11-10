"""Get Variant Attribute on a variant a subVI produces: the empty-variant query
follows the wire into the called VI, and back out to the caller for a control."""

from __future__ import annotations

import re

import pytest

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
_GAP = "Get Variant Attribute on a variant that is not empty"


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


def test_a_subvi_built_table_is_a_dict(project: Project) -> None:  # noqa: F811
    project.add(_builder("Build.vi"))
    project.add(_caller_of("Build.vi"))
    package = project.generate(NAME)
    result = package.function("colour", "red", "colour")
    assert (result.value, result.found) == ("red", True)


def test_a_table_built_two_subvis_down_is_a_dict(
    project: Project,  # noqa: F811
) -> None:
    project.add(_builder("Build.vi"))
    project.add(_wrapper("Wrap.vi", "Build.vi"))
    project.add(_caller_of("Wrap.vi"))
    package = project.generate(NAME)
    result = package.function("colour", "red", "colour")
    assert (result.value, result.found) == ("red", True)


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


def test_a_passthrough_of_an_empty_built_variant_is_a_dict(
    project: Project,  # noqa: F811
) -> None:
    project.add(_passthrough("Pass.vi"))
    project.add(_passes_variant("Pass.vi", wired="empty"))
    package = project.generate(NAME)
    result = package.function("colour", "red", "colour")
    assert (result.value, result.found) == ("red", True)


def test_a_passthrough_of_a_parameter_of_the_top_vi_is_a_dict(
    project: Project,  # noqa: F811
) -> None:
    """The control is on the connector pane of the VI the lookup is in: a
    parameter, the dict its caller passes, carried through the passthrough."""
    project.add(_passthrough("Pass.vi"))
    project.add(_passes_variant("Pass.vi", wired="control", pane=True))
    package = project.generate(NAME)
    assert package.function({"colour": "red"}, "colour").value == "red"


def test_a_passthrough_of_a_control_off_the_pane_is_a_gap(project: Project) -> None:  # noqa: F811
    project.add(_passthrough("Pass.vi"))
    project.add(_passes_variant("Pass.vi", wired="control"))
    package = project.generate(NAME)
    with pytest.raises(NotImplementedError, match=re.escape(_GAP)):
        package.function("a")


def test_one_passthrough_called_empty_and_with_a_control_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    """The callee's control is traced from each call: one call wires a control, so
    the table is not provably empty even though another call wires an empty one."""
    project.add(_passthrough("Pass.vi"))
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    first = vi.subvi(
        "Pass.vi", variant=(0, variant_type, False), table=(1, variant_type, True)
    )
    vi.wire(vi.constant(variant_type, EMPTY_VARIANT), first["variant"])
    second = vi.subvi(
        "Pass.vi", variant=(0, variant_type, False), table=(1, variant_type, True)
    )
    vi.wire(vi.control("given", variant_type, None), second["variant"])
    _get_from(vi, variant_type, second["table"])
    project.add(vi)
    package = project.generate(NAME)
    with pytest.raises(NotImplementedError, match=re.escape(_GAP)):
        package.function("a")


def test_a_subvi_that_is_not_loaded_is_a_gap(project: Project) -> None:  # noqa: F811
    project.add(_caller_of("Missing.vi"))
    package = project.generate(NAME)
    assert _GAP in package.source


def test_a_recursive_subvi_ends_as_a_gap(project: Project) -> None:  # noqa: F811
    """A VI that passes the table to itself has no source of its own: not empty."""
    vi = Vi("Loop.vi")
    variant_type = vi.add_type(_VARIANT)
    variant = vi.control("variant", variant_type, REQUIRED)
    table = vi.indicator("table", variant_type)
    call = vi.subvi(
        "Loop.vi", variant=(0, variant_type, False), table=(1, variant_type, True)
    )
    vi.wire(variant, call["variant"])
    vi.wire(call["table"], table)
    project.add(vi)
    project.add(_passes_variant("Loop.vi", wired="empty"))
    package = project.generate(NAME)
    assert _GAP in package.source
