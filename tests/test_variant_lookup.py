"""Set, Get and Delete Variant Attribute on a ``Variant``: its attributes, each a
``Variant``. The variant is the wire's own object, a copy only where the wire forks
(``owned_input``); a value fed from a typed wire is a ``Variant`` of that wire's
type. Each case is a tiny diagram, generated and run."""

from __future__ import annotations

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
# An empty Variant holding the attributes 'a' (a Variant of the 32-bit integer 1)
# and 'b' (a Variant of the string 'x').
ATTRIBUTED_VARIANT = (
    "09008000000000010004000000010000"
    "00000002"
    "00000001"
    "61"
    "09008000"
    "00000002"
    "00040000"
    "00040003"
    "0001"
    "0001"
    "00000001"
    "00000000"
    "00000001"
    "62"
    "09008000"
    "00000002"
    "00040000"
    "00080030FFFFFFFF"
    "0001"
    "0001"
    "00000001"
    "78"
    "00000000"
)


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


def _table(package, **attributes):
    """The ``Variant`` of no value that holds ``attributes`` (name to the Variant of
    that string)."""
    variants = package.variants
    return variants.Variant(
        attributes=
        {k: variants.Variant(v, variants.LV_String) for k, v in attributes.items()}
    )


def test_get_finds_an_attribute_set_on_an_empty_variant(
    project: Project,  # noqa: F811
) -> None:
    package = _get(project, name=True)
    assert "getattr" not in package.source
    assert "lvkit" not in package.source
    hit = package.function("colour", "red", "colour")
    colour = package.variants.Variant("red", package.variants.LV_String)
    assert (hit.value, hit.found) == (colour, True)
    assert hit.duplicate == _table(package, colour="red")
    miss = package.function("colour", "red", "size")
    assert (miss.value, miss.found) == (package.variants.Variant(), False)


def test_get_reads_an_attribute_of_a_constant_that_has_attributes(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(8205, _get_pane(variant_type))
    vi.wire(vi.constant(variant_type, ATTRIBUTED_VARIANT), node["variant"])
    vi.wire(vi.control("lookup", STR, REQUIRED), node["name"])
    vi.wire(node["value"], vi.indicator("value", variant_type))
    vi.wire(node["found"], vi.indicator("found", variant_type))
    vi.wire(node["duplicate"], vi.indicator("duplicate", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    assert "lvkit" not in package.source
    variants = package.variants
    hit = package.function("b")
    assert (hit.value, hit.found) == (variants.Variant("x", variants.LV_String), True)
    assert hit.duplicate == variants.Variant(
        attributes={
            "a": variants.Variant(1, variants.LV_I32),
            "b": variants.Variant("x", variants.LV_String),
        }
    )
    miss = package.function("c")
    assert (miss.value, miss.found) == (variants.Variant(), False)


def test_get_without_a_name_returns_all_names_and_values_sorted(
    project: Project,  # noqa: F811
) -> None:
    package = _get(project, name=False)
    result = package.function("colour", "red")
    assert (result.found, result.value) == (
        ["colour"],
        [package.variants.Variant("red", package.variants.LV_String)],
    )


def test_get_without_a_name_lists_the_names_in_ascending_order(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(8205, _get_pane(variant_type))
    vi.wire(vi.control("variant", variant_type, REQUIRED), node["variant"])
    vi.wire(node["value"], vi.indicator("value", variant_type))
    vi.wire(node["found"], vi.indicator("names", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    variants = package.variants
    given = variants.Variant(
        attributes={
            "b": variants.Variant(2, variants.LV_I32),
            "a": variants.Variant(1, variants.LV_I32),
            "c": variants.Variant(3, variants.LV_I32),
        }
    )
    result = package.function(given)
    assert result.names == ["a", "b", "c"]
    assert [v.value for v in result.value] == [1, 2, 3]


def test_delete_with_a_name_removes_that_attribute(
    project: Project,  # noqa: F811
) -> None:
    package = _delete(project)
    hit = package.function("colour", "red", "colour")
    assert (hit.found, hit.result) == (True, package.variants.Variant())
    miss = package.function("colour", "red", "size")
    assert (miss.found, miss.result) == (False, _table(package, colour="red"))


def test_delete_with_an_empty_name_removes_all_attributes(
    project: Project,  # noqa: F811
) -> None:
    package = _delete(project)
    result = package.function("colour", "red", "")
    assert (result.found, result.result) == (True, package.variants.Variant())


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
    assert (result.replaced, result.result) == (True, _table(package, a="2"))


def test_a_shift_register_started_empty_holds_the_attributes_set(
    project: Project,  # noqa: F811
) -> None:
    """The variant a loop's shift register starts as the empty Variant constant
    and feeds back from Set Variant Attribute."""
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
    assert package.function("a", "1").result == _table(package, a="1")


def _get_from_parameter(project: Project, *, rule: int | None):  # noqa: F811
    """Get Variant Attribute on a Variant control: on the connector pane when
    ``rule`` is given, off it when None."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(8205, _get_pane(variant_type))
    vi.wire(vi.control("variant", variant_type, rule), node["variant"])
    vi.wire(vi.control("lookup", STR, REQUIRED), node["name"])
    vi.wire(node["value"], vi.indicator("value", variant_type))
    vi.wire(node["found"], vi.indicator("found", variant_type))
    project.add(vi)
    return project.generate(NAME)


def test_get_on_a_variant_parameter_reads_the_variant_passed(
    project: Project,  # noqa: F811
) -> None:
    """The shape of a VIPM hook VI: a Variant control on the connector pane."""
    package = _get_from_parameter(project, rule=REQUIRED)
    assert "lvkit" not in package.source
    given = _table(package, **{"Package Name": "x"})
    hit = package.function(given, "Package Name")
    assert (hit.value, hit.found) == (
        package.variants.Variant("x", package.variants.LV_String),
        True,
    )
    miss = package.function(given, "Other")
    assert (miss.value, miss.found) == (package.variants.Variant(), False)


def test_delete_on_a_variant_parameter_returns_the_variant_without_the_name(
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
    result = package.function(_table(package, a="1", b="2"), "a")
    assert result.result == _table(package, b="2")


def test_a_get_on_a_variant_control_off_the_pane_reads_its_empty_default(
    project: Project,  # noqa: F811
) -> None:
    package = _get_from_parameter(project, rule=None)
    assert package.gaps == []
    result = package.function("a")
    assert (result.value, result.found) == (package.variants.Variant(), False)


def test_a_set_after_a_loop_feeds_a_value_back_is_not_a_gap(
    project: Project,  # noqa: F811
) -> None:
    """The register starts empty and a control off the pane feeds it back; the Set
    edits whatever Variant the register holds."""
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
    assert package.gaps == []
    assert "NotImplementedError" not in package.source
    package.function("a", "1")


def test_a_get_passes_its_variant_on_to_a_set(
    project: Project,  # noqa: F811
) -> None:
    """Get's variant output is its variant input: a Set after it adds to it."""
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
    result = package.function(_table(package, a="1"), "a", "b", "2")
    assert result.result == _table(package, a="1", b="2")


def test_a_set_on_one_branch_leaves_the_other_branch_unchanged(
    project: Project,  # noqa: F811
) -> None:
    """The wire from ``variant`` forks to a Set and to an indicator: the Set works
    on a copy, so the other branch (and the caller's object) keep their
    attributes."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = vi.primitive(8204, _set_pane(variant_type))
    vi.wire(
        vi.control("variant", variant_type, REQUIRED),
        node["variant"],
        vi.indicator("kept", variant_type),
    )
    vi.wire(vi.control("key", STR, REQUIRED), node["name"])
    vi.wire(vi.control("content", STR, REQUIRED), node["value"])
    vi.wire(node["variant out"], vi.indicator("changed", variant_type))
    project.add(vi)
    package = project.generate(NAME)
    given = _table(package, a="1")
    result = package.function(given, "b", "2")
    assert result.kept == _table(package, a="1")
    assert result.changed == _table(package, a="1", b="2")
    assert given == _table(package, a="1")
