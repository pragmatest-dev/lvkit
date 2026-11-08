"""Set, Get and Delete Variant Attribute on an empty variant: a ``dict``, decided
at generation by following the variant wire upstream."""

from __future__ import annotations

import re

import pytest

from tests.error_diagrams import (
    BOOL,
    ERR,
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Table.vi"
_VARIANT = '<TypeDesc Type="Variant" Format="inline" />'
EMPTY_VARIANT = "0900800000000001000400000001000000000000"


def _set_pane(variant_type: int) -> list[tuple[str, int, int, bool]]:
    return [
        ("error out", 0, ERR, True),
        ("replaced", 2, BOOL, True),
        ("variant out", 3, variant_type, True),
        ("error in", 4, ERR, False),
        ("value", 5, STR, False),
        ("name", 6, STR, False),
        ("variant", 7, variant_type, False),
    ]


def _get_pane(variant_type: int) -> list[tuple[str, int, int, bool]]:
    return [
        ("error out", 0, ERR, True),
        ("value", 1, variant_type, True),
        ("found", 2, variant_type, True),
        ("duplicate", 3, variant_type, True),
        ("error in", 4, ERR, False),
        ("default", 5, variant_type, False),
        ("name", 6, STR, False),
        ("variant", 7, variant_type, False),
    ]


def _delete_pane(variant_type: int) -> list[tuple[str, int, int, bool]]:
    return [
        ("error out", 0, ERR, True),
        ("found", 2, BOOL, True),
        ("variant out", 3, variant_type, True),
        ("error in", 4, ERR, False),
        ("name", 6, STR, False),
        ("variant", 7, variant_type, False),
    ]


def _set_then(vi: Vi, variant_type: int, end: dict[str, int]) -> None:
    """Set ``name`` = ``value`` on an empty Variant constant and wire the result
    to ``end``'s variant."""
    node = vi.primitive(8204, _set_pane(variant_type))
    vi.wire(vi.constant(variant_type, EMPTY_VARIANT), node["variant"])
    vi.wire(vi.control("key", STR, REQUIRED), node["name"])
    vi.wire(vi.control("content", STR, REQUIRED), node["value"])
    vi.wire(node["variant out"], end["variant"])
    vi.wire(node["error out"], vi.error_out())


def _get(project: Project, *, name: bool):  # noqa: F811
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(8205, _get_pane(variant_type))
    _set_then(vi, variant_type, node)
    if name:
        vi.wire(vi.control("lookup", STR, REQUIRED), node["name"])
    vi.wire(node["value"], vi.indicator("value", variant_type))
    vi.wire(node["found"], vi.indicator("found", variant_type))
    vi.wire(node["duplicate"], vi.indicator("duplicate", variant_type))
    project.add(vi)
    return project.generate(NAME)


def _delete(project: Project):  # noqa: F811
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(8206, _delete_pane(variant_type))
    _set_then(vi, variant_type, node)
    vi.wire(vi.control("lookup", STR, REQUIRED), node["name"])
    vi.wire(node["found"], vi.indicator("found", BOOL))
    vi.wire(node["variant out"], vi.indicator("result", variant_type))
    project.add(vi)
    return project.generate(NAME)


def test_get_finds_an_attribute_set_on_an_empty_variant(
    project: Project,  # noqa: F811
) -> None:
    package = _get(project, name=True)
    assert "getattr" not in package.source
    assert "lvkit" not in package.source
    hit = package.function("colour", "red", "colour")
    assert (hit.value, hit.found, hit.duplicate) == ("red", True, {"colour": "red"})
    miss = package.function("colour", "red", "size")
    assert (miss.value, miss.found) == (None, False)


def test_get_without_a_name_returns_all_names_and_values_sorted(
    project: Project,  # noqa: F811
) -> None:
    package = _get(project, name=False)
    result = package.function("colour", "red")
    assert (result.found, result.value) == (["colour"], ["red"])


def test_delete_with_a_name_removes_that_attribute(
    project: Project,  # noqa: F811
) -> None:
    package = _delete(project)
    hit = package.function("colour", "red", "colour")
    assert (hit.found, hit.result) == (True, {})
    miss = package.function("colour", "red", "size")
    assert (miss.found, miss.result) == (False, {"colour": "red"})


def test_delete_with_an_empty_name_removes_all_attributes(
    project: Project,  # noqa: F811
) -> None:
    package = _delete(project)
    result = package.function("colour", "red", "")
    assert (result.found, result.result) == (True, {})


def test_replaced_is_true_for_a_second_set_of_the_same_name(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    second = vi.primitive(8204, _set_pane(variant_type))
    _set_then(vi, variant_type, second)
    vi.wire(vi.control("key2", STR, REQUIRED), second["name"])
    vi.wire(vi.control("content2", STR, REQUIRED), second["value"])
    vi.wire(second["replaced"], vi.indicator("replaced", BOOL))
    vi.wire(second["variant out"], vi.indicator("result", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    result = package.function("a", "1", "a", "2")
    assert (result.replaced, result.result) == (True, {"a": "2"})


def test_a_shift_register_started_empty_holds_a_dict(
    project: Project,  # noqa: F811
) -> None:
    """The variant a loop's shift register starts as the empty Variant constant
    and feeds back from Set Variant Attribute: a lookup table."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    loop = vi.loop("forLoop", shift_registers=[variant_type])
    register = loop.shift_registers[0]
    vi.wire(vi.int_constant(2), loop.selector)
    vi.wire(vi.constant(variant_type, EMPTY_VARIANT), register.left_outer)
    with vi.inside(loop.scopes[0]):
        node = vi.primitive(8204, _set_pane(variant_type))
        vi.wire(register.left_face, node["variant"])
        vi.wire(node["variant out"], register.right_face)
        vi.wire(vi.control("key", STR, REQUIRED), node["name"])
        vi.wire(vi.control("content", STR, REQUIRED), node["value"])
    vi.wire(register.right_outer, vi.indicator("result", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    assert package.function("a", "1").result == {"a": "1"}


def _get_from_parameter(project: Project, *, rule: int | None):  # noqa: F811
    """Get Variant Attribute on a Variant control: on the connector pane when
    ``rule`` is given, off it when None."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(8205, _get_pane(variant_type))
    vi.wire(vi.control("variant", variant_type, rule), node["variant"])
    vi.wire(vi.control("lookup", STR, REQUIRED), node["name"])
    vi.wire(node["value"], vi.indicator("value", variant_type))
    project.add(vi)
    return project.generate(NAME)


def test_get_on_a_variant_parameter_reads_the_dict_passed(
    project: Project,  # noqa: F811
) -> None:
    """The shape of a VIPM hook VI: a Variant control on the connector pane."""
    package = _get_from_parameter(project, rule=REQUIRED)
    assert "lvkit" not in package.source
    assert "a dict of the variant's attributes" in package.source
    assert package.function({"Package Name": "x"}, "Package Name").value == "x"
    assert package.function({"Package Name": "x"}, "Other").value is None


def test_delete_on_a_variant_parameter_returns_the_dict_without_the_name(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(8206, _delete_pane(variant_type))
    vi.wire(vi.control("variant", variant_type, REQUIRED), node["variant"])
    vi.wire(vi.control("lookup", STR, REQUIRED), node["name"])
    vi.wire(node["variant out"], vi.indicator("result", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    result = package.function({"a": "1", "b": "2"}, "a")
    assert result.result == {"b": "2"}


def test_a_get_on_a_variant_control_off_the_pane_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(8205, _get_pane(variant_type))
    vi.wire(vi.control("variant", variant_type, None), node["variant"])
    vi.wire(vi.control("lookup", STR, REQUIRED), node["name"])
    vi.wire(node["value"], vi.indicator("value", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    reason = "Get Variant Attribute on a variant that is not empty"
    with pytest.raises(NotImplementedError, match=re.escape(reason)):
        package.function("a")


def test_a_shift_register_fed_back_a_value_is_not_empty(
    project: Project,  # noqa: F811
) -> None:
    """The register starts empty, but a control off the pane feeds it back: not a
    lookup table."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    loop = vi.loop("forLoop", shift_registers=[variant_type])
    register = loop.shift_registers[0]
    vi.wire(vi.int_constant(2), loop.selector)
    vi.wire(vi.constant(variant_type, EMPTY_VARIANT), register.left_outer)
    with vi.inside(loop.scopes[0]):
        node = vi.primitive(8204, _set_pane(variant_type))
        vi.wire(register.left_face, node["variant"])
        vi.wire(vi.control("fed", variant_type, None), register.right_face)
        vi.wire(vi.control("key", STR, REQUIRED), node["name"])
        vi.wire(vi.control("content", STR, REQUIRED), node["value"])
    project.add(vi)
    package = project.generate(NAME)
    reason = "Set Variant Attribute on a variant that is not empty"
    assert reason in package.source


def test_a_get_passes_its_variant_on_as_the_same_table(
    project: Project,  # noqa: F811
) -> None:
    """Get's variant output is its variant input: a Set after it still edits the
    dict passed in."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    get = vi.primitive(8205, _get_pane(variant_type))
    vi.wire(vi.control("variant", variant_type, REQUIRED), get["variant"])
    vi.wire(vi.control("lookup", STR, REQUIRED), get["name"])
    node = vi.primitive(8204, _set_pane(variant_type))
    vi.wire(get["duplicate"], node["variant"])
    vi.wire(vi.control("key", STR, REQUIRED), node["name"])
    vi.wire(vi.control("content", STR, REQUIRED), node["value"])
    vi.wire(node["variant out"], vi.indicator("result", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    result = package.function({"a": "1"}, "a", "b", "2")
    assert result.result == {"a": "1", "b": "2"}
