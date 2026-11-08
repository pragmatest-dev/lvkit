"""Set Variant Attribute (8204): an attribute set on a copy of the variant.

Uses the one representation Get Variant Attribute has: a variant is the Python
value itself and an attribute is an attribute of it.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from lvkit.primitive_resolver import get_resolver
from tests.error_diagrams import (
    BOOL,
    ERR,
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Tag.vi"
_VARIANT = '<TypeDesc Type="Variant" Format="inline" />'


def _pane(vi: Vi, variant_type: int) -> list[tuple[str, int, int, bool]]:
    return [
        ("error out", 0, ERR, True),
        ("replaced", 2, BOOL, True),
        ("variant out", 3, variant_type, True),
        ("error in", 4, ERR, False),
        ("value", 5, STR, False),
        ("name", 6, STR, False),
        ("variant", 7, variant_type, False),
    ]


def _set(project: Project, *, variant_wired: bool = True, value: bool = True):  # noqa: F811
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    variant = vi.control("variant", variant_type, REQUIRED)
    attr = vi.control("attr", STR, REQUIRED)
    content = vi.control("content", STR, REQUIRED)
    replaced = vi.indicator("replaced", BOOL)
    result = vi.indicator("result", variant_type)
    out = vi.error_out()
    node = vi.primitive(8204, _pane(vi, variant_type))
    if variant_wired:
        vi.wire(variant, node["variant"])
    vi.wire(attr, node["name"])
    if value:
        vi.wire(content, node["value"])
    vi.wire(node["replaced"], replaced)
    vi.wire(node["variant out"], result)
    vi.wire(node["error out"], out)
    project.add(vi)
    return project.generate(NAME)


def test_resolver_terminals() -> None:
    entry = get_resolver().resolve(prim_id=8204)
    assert entry is not None
    assert [(t.index, t.direction, t.name) for t in entry.terminals] == [
        (0, "output", "error_out"),
        (2, "output", "replaced"),
        (3, "output", "variant_out"),
        (4, "input", "error_in"),
        (5, "input", "value"),
        (6, "input", "name"),
        (7, "input", "variant"),
    ]


def test_describe_and_lvnet_name_the_function(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    vi.primitive(8204, _pane(vi, vi.add_type(_VARIANT)))
    project.add(vi)
    views = project.views(NAME)
    assert "Set Variant Attribute [prim 8204]" in views.describe
    assert ": Set Variant Attribute\n" in views.lvnet


def test_a_new_attribute_is_created_on_a_copy(project: Project) -> None:  # noqa: F811
    package = _set(project)
    assert "lvkit" not in package.source
    original = SimpleNamespace(kept=1)
    result = package.function(original, "colour", "red")
    assert (result.replaced, result.result.colour) == (False, "red")
    assert result.result.kept == 1
    assert not hasattr(original, "colour")
    assert result.error_out is None


def test_an_existing_attribute_is_replaced(project: Project) -> None:  # noqa: F811
    package = _set(project)
    result = package.function(SimpleNamespace(colour="red"), "colour", "blue")
    assert (result.replaced, result.result.colour) == (True, "blue")


def test_an_empty_name_is_error_1(project: Project) -> None:  # noqa: F811
    package = _set(project)
    result = package.function(SimpleNamespace(), "", "x")
    assert result.error_out.code == 1
    assert result.error_out.source == "Set Variant Attribute"


def test_an_empty_variant_is_a_gap(project: Project) -> None:  # noqa: F811
    package = _set(project, variant_wired=False)
    reason = "Set Variant Attribute on an empty variant"
    assert reason in package.source
    with pytest.raises(NotImplementedError, match=re.escape(reason)):
        package.function(None, "a", "b")


def test_a_constant_variant_is_a_gap(project: Project) -> None:  # noqa: F811
    """The empty variant constant is written as a literal, which has no object to
    carry an attribute."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    attr = vi.control("attr", STR, REQUIRED)
    content = vi.control("content", STR, REQUIRED)
    out = vi.error_out()
    node = vi.primitive(8204, _pane(vi, variant_type))
    vi.wire(vi.constant(STR, "00000002" + "4142".upper()), node["variant"])
    vi.wire(attr, node["name"])
    vi.wire(content, node["value"])
    vi.wire(node["error out"], out)
    project.add(vi)
    package = project.generate(NAME)
    reason = "Set Variant Attribute on an empty variant"
    assert reason in package.source
    with pytest.raises(NotImplementedError, match=re.escape(reason)):
        package.function("a", "b")
