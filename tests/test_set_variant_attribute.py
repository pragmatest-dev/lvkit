"""Set Variant Attribute (8204): an empty variant given attributes is a dict of
them; a variant that does not start empty is a gap.
"""

from __future__ import annotations

import re

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


EMPTY_VARIANT = "0900800000000001000400000001000000000000"


def _set(project: Project, *, source: str = "constant", value: bool = True):  # noqa: F811
    """Set Variant Attribute whose variant is ``source``: an empty Variant
    ``constant``, an ``unwired`` input, a connector-pane ``control`` or a control
    that is ``off-pane``."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    attr = vi.control("attr", STR, REQUIRED)
    content = vi.control("content", STR, REQUIRED)
    replaced = vi.indicator("replaced", BOOL)
    result = vi.indicator("result", variant_type)
    out = vi.error_out()
    node = vi.primitive(8204, _pane(vi, variant_type))
    if source == "control":
        vi.wire(vi.control("variant", variant_type, REQUIRED), node["variant"])
    elif source == "off-pane":
        vi.wire(vi.control("variant", variant_type, None), node["variant"])
    elif source == "constant":
        vi.wire(vi.constant(variant_type, EMPTY_VARIANT), node["variant"])
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
        (0, "output", "error out"),
        (2, "output", "replaced"),
        (3, "output", "Variant out"),
        (4, "input", "error in"),
        (5, "input", "value"),
        (6, "input", "name"),
        (7, "input", "Variant"),
    ]


def test_describe_and_lvnet_name_the_function(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    vi.primitive(8204, _pane(vi, vi.add_type(_VARIANT)))
    project.add(vi)
    views = project.views(NAME)
    assert "Set Variant Attribute [prim 8204]" in views.describe
    assert ": Set Variant Attribute\n" in views.lvnet


def test_an_empty_variant_constant_given_an_attribute_is_a_dict(
    project: Project,  # noqa: F811
) -> None:
    package = _set(project)
    assert "lvkit" not in package.source
    result = package.function("colour", "red")
    assert (result.replaced, result.result) == (False, {"colour": "red"})
    assert result.error_out is None


def test_an_unwired_variant_given_an_attribute_is_a_dict(
    project: Project,  # noqa: F811
) -> None:
    package = _set(project, source="unwired")
    result = package.function("colour", "red")
    assert (result.replaced, result.result) == (False, {"colour": "red"})


def test_an_empty_name_is_error_1(project: Project) -> None:  # noqa: F811
    package = _set(project)
    result = package.function("", "x")
    assert result.error_out.code == 1
    assert result.error_out.source == "Set Variant Attribute"


def test_a_variant_parameter_given_an_attribute_is_the_dict_passed(
    project: Project,  # noqa: F811
) -> None:
    package = _set(project, source="control")
    assert "a dict of the variant's attributes" in package.source
    result = package.function("colour", "red", {"size": "big"})
    assert result.result == {"size": "big", "colour": "red"}


def test_a_variant_control_off_the_pane_is_a_gap(project: Project) -> None:  # noqa: F811
    package = _set(project, source="off-pane")
    reason = "Set Variant Attribute on a variant that is not empty"
    assert reason in package.source
    with pytest.raises(NotImplementedError, match=re.escape(reason)):
        package.function("a", "b")
