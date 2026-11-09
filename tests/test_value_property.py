"""A Property Node bound to a control of the VI whose rows are ``Value`` reads
and writes the control's variable the way a Local Variable does
(``codegen/nodes/property_node.py``, ``codegen/nodes/local_variable.py``); a row
that writes a listed control property (``control_properties.json``) calls
``on_property``; a node with any other row stays a gap. Each case is a tiny
diagram, generated and called with recording hooks."""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from typing import Any

import pytest

from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.panel_diagrams import value_property
from tests.panel_harness import panel_of, run_once, simulate

NAME = "Prop.vi"


def _body(package: Package) -> str:
    source = package.source
    start = source.index(f"def {NAME.removesuffix('.vi').lower()}(")
    lines = source[start:].splitlines()[1:]
    return textwrap.dedent("\n".join(lines))


def _read(vi: Vi) -> None:
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    (value,) = value_property(vi, n, ["Value"])
    vi.wire(value, out)


def _write(vi: Vi) -> None:
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    (value,) = value_property(vi, n, ["Value"])
    five = vi.int_constant(5)
    vi.wire(five, value)
    vi.wire(vi.int_constant(0), out)


def test_a_value_read_is_the_control_variable(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    _read(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert "on_read(" not in package.source
    assert package.function(n=4).out == 4


def test_a_value_write_assigns_the_control_and_updates(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _write(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert "n = 5\n" in _body(package)
    assert "on_update(PropValue.N, n)" in package.source
    calls: list[tuple[str, Any]] = []
    package.function(n=1, on_update=lambda name, v: calls.append((name, v)))
    assert ("n", 5) in calls
    assert package.function(n=1).out == 0


def test_a_value_write_then_read_in_a_loop(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    seen = vi.indicator("seen", I32)
    seven = vi.int_constant(7)
    (write,) = value_property(vi, n, ["Value"])
    vi.wire(seven, write)
    loop = vi.loop("whileLoop")
    with vi.inside(loop.scopes[0]):
        (read,) = value_property(vi, n, ["Value"])
        stop = vi.bool_constant(True)
    vi.place_inside(seen, loop.scopes[0])
    vi.wire(read, seen)
    vi.wire(stop, loop.faces[0])
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    events: list[tuple[str, str, Any]] = []

    def update(name: str, value: Any) -> None:
        events.append(("update", name, value))

    def read_hook(name: str, value: Any) -> Any:
        events.append(("read", name, value))
        return 40

    result = package.function(1, on_update=update, on_read=read_hook)
    assert result.seen == 40
    assert events == [("update", "n", 7), ("read", "n", 7), ("update", "seen", 40)]
    assert package.function(n=1).seen == 7


def _other_row_vi(row: str | tuple[str, int]) -> Vi:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    value, other = value_property(vi, n, ["Value", row])
    vi.wire(value, out)
    vi.wire(vi.bool_constant(True), other)
    return vi


def test_a_node_with_a_row_that_is_not_listed_stays_a_gap(
    project: Project,  # noqa: F811
) -> None:
    project.add(_other_row_vi(("Visible", 104013825)))
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "Implicit property reference requires a binding model" in str(
        package.gaps[0]
    )
    with pytest.raises(NotImplementedError, match="binding model"):
        package.function(n=1)


def _properties(vi: Vi, *, on_indicator: bool = False) -> None:
    """A node writing DisabledEnum (2) then Visible (False) on the control ``n``,
    or on the indicator ``out``."""
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    disabled, visible = value_property(
        vi, out if on_indicator else n, ["DisabledEnum", "Visible"]
    )
    vi.wire(vi.int_constant(2), disabled)
    vi.wire(vi.bool_constant(False), visible)
    vi.wire(n, out)


@pytest.mark.parametrize("on_indicator", [False, True])
def test_listed_property_writes_call_on_property_in_row_order(
    on_indicator: bool,
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _properties(vi, on_indicator=on_indicator)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert "class PropProperty(str, Enum)" in package.source
    assert "on_property: Callable[[PropValue, PropProperty, Any], None] | None" in (
        package.source
    )
    shown = "OUT" if on_indicator else "N"
    calls: list[tuple[Any, Any, Any]] = []
    result = package.function(
        n=3, on_property=lambda *call: calls.append(call)
    )
    assert result.out == 3
    value = package.module.PropValue
    prop = package.module.PropProperty
    assert calls == [
        (getattr(value, shown), prop.DISABLED, 2),
        (getattr(value, shown), prop.VISIBLE, False),
    ]
    assert package.function(n=3).out == 3


def test_a_value_write_and_a_listed_write_run_in_row_order(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    value, visible = value_property(vi, n, ["Value", "Visible"])
    vi.wire(vi.int_constant(5), value)
    vi.wire(vi.bool_constant(True), visible)
    vi.wire(vi.int_constant(0), out)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    events: list[tuple[Any, ...]] = []
    package.function(
        n=1,
        on_update=lambda name, v: events.append(("update", name, v)),
        on_property=lambda name, prop, v: events.append(("property", name, prop, v)),
    )
    prop = package.module.PropProperty
    assert [e for e in events if e[1] == "n"] == [
        ("update", "n", 5),
        ("property", "n", prop.VISIBLE, True),
    ]


def test_the_property_code_decides_not_the_stored_name(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    (disabled,) = value_property(vi, n, [("Disabled", 104013885)])
    vi.wire(vi.int_constant(1), disabled)
    vi.wire(n, out)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    calls: list[tuple[Any, Any, Any]] = []
    package.function(n=3, on_property=lambda *call: calls.append(call))
    assert calls == [("n", package.module.PropProperty.DISABLED, 1)]


@pytest.mark.parametrize(
    "row", ["Value (Signaling)", ("Visible", 104013825), ("Frame Color", 104013830)]
)
def test_a_property_that_is_not_listed_is_a_gap(
    row: str | tuple[str, int],
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    (other,) = value_property(vi, n, [row])
    vi.wire(vi.bool_constant(True), other)
    vi.wire(n, out)
    project.add(vi)
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "binding model" in str(package.gaps[0])


def test_a_read_of_a_listed_property_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    seen = vi.indicator("seen", BOOL, ddo_class="stdBool")
    (visible,) = value_property(vi, n, ["Visible"])
    vi.wire(visible, seen)
    project.add(vi)
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "binding model" in str(package.gaps[0])


def test_an_unwired_listed_write_is_a_gap(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    value_property(vi, n, ["Visible"])
    vi.wire(n, out)
    project.add(vi)
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "binding model" in str(package.gaps[0])


def test_a_vi_with_only_value_rows_has_no_property_hook(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _write(vi)
    project.add(vi)
    package = project.generate(NAME)
    assert "on_property" not in package.source
    assert "Property" not in package.source


def test_the_panel_of_a_vi_that_writes_a_property_builds_and_runs(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    _properties(vi)
    project.add(vi)
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, "prop")

    async def scenario(user: Any) -> None:
        await run_once(user)

    simulate(panel.build_panel, scenario)


@pytest.mark.parametrize("build", [_read, _write, _properties])
def test_the_views_of_a_vi_with_a_value_property_node_are_unchanged(
    build: Callable[[Vi], None],
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    build(vi)
    project.add(vi)
    before = project.views(NAME)
    project.generate(NAME)
    after = project.views(NAME)
    for view in ("lvnet", "terse", "describe", "json"):
        assert getattr(after, view) == getattr(before, view)
        for hook in (
            "on_read",
            "on_update",
            "on_property",
            "PropValue",
            "PropProperty",
        ):
            assert hook not in str(getattr(after, view))


def test_a_node_bound_to_a_part_of_a_type_defined_control_is_the_control(
    project: Project,  # noqa: F811
) -> None:
    """The bound object is inside the control (the saved object of a type-defined
    control holds the control itself): the node is tied to the control around it."""
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED, ddo_class="typeDef")
    seen = vi.indicator("seen", I32)
    seven = vi.int_constant(7)
    (write,) = value_property(vi, n, ["Value"], inner=True)
    vi.wire(seven, write)
    loop = vi.loop("whileLoop")
    with vi.inside(loop.scopes[0]):
        (read,) = value_property(vi, n, ["Value"], inner=True)
        stop = vi.bool_constant(True)
    vi.place_inside(seen, loop.scopes[0])
    vi.wire(read, seen)
    vi.wire(stop, loop.faces[0])
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    events: list[tuple[str, str, Any]] = []

    def update(name: str, value: Any) -> None:
        events.append(("update", name, value))

    def read_hook(name: str, value: Any) -> Any:
        events.append(("read", name, value))
        return 40

    result = package.function(1, on_update=update, on_read=read_hook)
    assert result.seen == 40
    assert events == [
        ("update", "input_", 7),
        ("read", "input_", 7),
        ("update", "seen", 40),
    ]
