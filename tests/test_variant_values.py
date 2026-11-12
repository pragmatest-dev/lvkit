"""A variant in generated Python is a ``Variant`` (``variants.py``): its value, its
LabVIEW type and its attributes. Each case is a tiny diagram, generated and run
(``tests/error_diagrams.py``): a typed wire that meets a Variant terminal makes a
``Variant`` of the wire's type, Variant To Data judges it by that type, Equal?
compares type, value and attributes, an unwired Variant input is a new empty
``Variant`` for each call, and a Variant constant holds its value, type and
attributes."""

from __future__ import annotations

import pytest

from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    OPTIONAL,
    REQUIRED,
    STR,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Var.vi"
_VARIANT = '<TypeDesc Type="Variant" Format="inline" />'
_DBL = '<TypeDesc Type="NumFloat64" Prop1="0" Format="inline" />'
_EXT = '<TypeDesc Type="NumFloatExt" Prop1="0" Format="inline" />'
_COUNT_TYPEDEF = (
    '<TypeDesc Type="TypeDef">'
    '<TypeDesc Type="NumInt32" Nested="True" Format="inline" />'
    '<Label Text="Count.ctl" /></TypeDesc>'
)
EMPTY_VARIANT = "0900800000000001000400000001000000000000"

TAKE = """
    from types import SimpleNamespace

    def take(v=None):
        return SimpleNamespace(r=v)
"""


def _variant_hex(value: int, attributes: list[tuple[str, str]] = ()) -> str:
    """The saved value of a Variant constant holding the 32-bit integer ``value``
    and the attributes ``(name, nested saved value)``."""
    text = "09008000" "00000002" "00040000" "00040003" "0001" "0001"
    text += f"{value & 0xFFFFFFFF:08X}" f"{len(attributes):08X}"
    for name, nested in attributes:
        text += f"{len(name):08X}" + name.encode().hex() + nested
    return text


# --- 1: a typed wire into a Variant control makes a Variant of its type -------


def test_a_typed_wire_into_a_subvis_variant_control_is_a_variant_of_its_type(
    project: Project,  # noqa: F811
) -> None:
    caller = Vi(NAME)
    variant_type = caller.add_type(_VARIANT)
    call = caller.subvi(
        "Take.vi", v=(0, variant_type, False), r=(1, variant_type, True)
    )
    caller.wire(caller.int_constant(5), call["v"])
    caller.wire(call["r"], caller.indicator("seen", variant_type))
    project.add(caller)
    callee = Vi("Take.vi")
    callee.control("v", variant_type, REQUIRED)
    callee.indicator("r", variant_type)
    callee.extra_types = caller.extra_types
    project.add(callee)

    package = project.generate(NAME, {"take": TAKE})

    variants = package.variants
    assert package.function().seen == variants.Variant(5, variants.I32)
    assert "Variant(5, I32)" in package.source
    assert "lvkit" not in package.source


# --- 2: Variant To Data judges the variant by its LabVIEW type ----------------


def _to_data(project: Project, source: int | str, data: int | str) -> Package:  # noqa: F811
    """``x`` of type ``source`` through To Variant and Variant To Data into ``d``
    of type ``data`` (a type id, or a ``<TypeDesc>`` added to the VI), with the
    node's error out as the VI's ``error out``."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    source_id = vi.add_type(source) if isinstance(source, str) else source
    data_id = vi.add_type(data) if isinstance(data, str) else data
    x = vi.control("x", source_id, REQUIRED)
    to_variant = vi.primitive(
        8201, [("Variant", 0, variant_type, True), ("anything", 1, source_id, False)]
    )
    to_data = vi.primitive(
        8003,
        [
            ("error in", 0, ERR, False),
            ("error out", 3, ERR, True),
            ("Variant", 8, variant_type, False),
            ("type", 9, data_id, False),
            ("data", 11, data_id, True),
        ],
    )
    vi.wire(x, to_variant["anything"])
    vi.wire(to_variant["Variant"], to_data["Variant"])
    vi.wire(to_data["data"], vi.indicator("d", data_id))
    vi.wire(to_data["error out"], vi.error_out())
    project.add(vi)
    return project.generate(NAME)


@pytest.mark.parametrize(
    ("source", "data", "given", "expected"),
    [
        (I32, I32, 5, 5),
        # JKI failUnlessEqual reads an extended-precision number from a double.
        (_DBL, _EXT, 1.5, 1.5),
        # A floating-point number converts to an integer type, rounded.
        (_DBL, I32, 1.5, 2),
        (I32, _DBL, 3, 3.0),
        # A typedef of a number is a number type: a plain I32 converts to it.
        (I32, _COUNT_TYPEDEF, 7, 7),
    ],
)
def test_a_number_variant_converts_to_any_number_type(
    project: Project,  # noqa: F811
    source: int | str,
    data: int | str,
    given: float,
    expected: float,
) -> None:
    result = _to_data(project, source, data).function(given)
    assert result.d == expected
    assert result.error_out is None


@pytest.mark.parametrize(
    ("source", "data", "given", "default"),
    [(STR, I32, "x", 0), (BOOL, I32, True, 0), (I32, STR, 5, "")],
)
def test_a_variant_of_another_kind_of_type_is_error_91(
    project: Project,  # noqa: F811
    source: int,
    data: int,
    given: object,
    default: object,
) -> None:
    result = _to_data(project, source, data).function(given)
    assert result.d == default
    assert (result.error_out.code, result.error_out.source) == (91, "Variant To Data")


def test_an_empty_variant_is_error_91(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    to_data = vi.primitive(
        8003,
        [
            ("error in", 0, ERR, False),
            ("error out", 3, ERR, True),
            ("Variant", 8, variant_type, False),
            ("type", 9, I32, False),
            ("data", 11, I32, True),
        ],
    )
    vi.wire(vi.constant(variant_type, EMPTY_VARIANT), to_data["Variant"])
    vi.wire(to_data["data"], vi.indicator("d", I32))
    vi.wire(to_data["error out"], vi.error_out())
    project.add(vi)
    result = project.generate(NAME).function()
    assert (result.d, result.error_out.code) == (0, 91)


# --- 6: Equal? on variants ----------------------------------------------------


def test_equal_compares_the_type_the_value_and_the_attributes(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    equal = vi.primitive(
        1102,
        [
            ("x = y?", 0, BOOL, True),
            ("y", 1, variant_type, False),
            ("x", 2, variant_type, False),
        ],
    )
    vi.wire(vi.control("x", variant_type, REQUIRED), equal["x"])
    vi.wire(vi.control("y", variant_type, REQUIRED), equal["y"])
    vi.wire(equal["x = y?"], vi.indicator("same", BOOL))
    project.add(vi)
    package = project.generate(NAME)
    variants = package.variants
    variant, i32, dbl = variants.Variant, variants.I32, variants.DBL
    same = package.function
    assert same(variant(1, i32), variant(1.0, dbl)).same is False
    assert same(variant(1, i32), variant(2, i32)).same is False
    assert same(variant(1, i32), variant(1, i32)).same is True
    with_name = variant(1, i32, {"a": variant(1, i32)})
    assert same(variant(1, i32), with_name).same is False
    assert same(with_name, variant(1, i32, {"a": variant(1, i32)})).same is True
    assert same(variant(), variant()).same is True


# --- 8: a Variant constant holds its value, type and attributes ---------------


def test_a_variant_constant_with_attributes_is_a_variant_of_them(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    held = _variant_hex(7, [("a", _variant_hex(1))])
    vi.wire(vi.constant(variant_type, held), vi.indicator("out", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    variants = package.variants
    assert "Variant(7, I32, {'a': Variant(1, I32, {})})" in package.source
    assert package.function().out == variants.Variant(
        7, variants.I32, {"a": variants.Variant(1, variants.I32)}
    )


# --- 9: an unwired optional Variant input is a new empty Variant each call ----


def test_an_unwired_variant_input_is_a_new_empty_variant_for_each_call(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    vi.wire(vi.control("v", variant_type, OPTIONAL), vi.indicator("out", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    first, second = package.function().out, package.function().out
    assert first == second == package.variants.Variant()
    assert first is not second
    kept = package.variants.Variant(3, package.variants.I32)
    assert package.function(v=kept).out is kept


# --- 12: a Variant just made is never copied ----------------------------------


def test_a_variant_made_for_a_typed_wire_is_not_copied_before_it_is_changed(
    project: Project,  # noqa: F811
) -> None:
    """``x`` feeds the variant input of Set Variant Attribute and an indicator
    too, so the wire forks: the Variant made for the typed wire is new, and no copy
    of it is made."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    x = vi.control("x", I32, REQUIRED)
    node = vi.primitive(
        8204,
        [
            ("error out", 0, ERR, True),
            ("replaced", 2, BOOL, True),
            ("variant out", 3, variant_type, True),
            ("error in", 4, ERR, False),
            ("value", 5, STR, False),
            ("name", 6, STR, False),
            ("variant", 7, variant_type, False),
        ],
    )
    vi.wire(x, node["variant"], vi.indicator("shown", I32))
    vi.wire(vi.control("key", STR, REQUIRED), node["name"])
    vi.wire(vi.control("content", STR, REQUIRED), node["value"])
    vi.wire(node["variant out"], vi.indicator("result", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    assert "copy.copy(Variant(" not in package.source
    out = package.function(7, "a", "b")
    variants = package.variants
    assert out.shown == 7
    assert out.result == variants.Variant(
        7, variants.I32, {"a": variants.Variant("b", variants.String)}
    )
