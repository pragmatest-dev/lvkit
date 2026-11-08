"""A Property Node bound to a control of the VI whose rows are all ``Value`` reads
and writes the control's variable the way a Local Variable does
(``codegen/nodes/property_node.py``, ``codegen/nodes/local_variable.py``); a node
with any other row stays a gap. Each case is a tiny diagram, generated and called
with recording hooks."""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from typing import Any

import pytest

from tests.error_diagrams import (
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.panel_diagrams import value_property

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


def test_a_node_with_another_row_stays_a_gap(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    value, visible = value_property(vi, n, ["Value", "Visible"])
    vi.wire(value, out)
    flag = vi.bool_constant(True)
    vi.wire(flag, visible)
    project.add(vi)
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "Implicit property reference requires a binding model" in str(
        package.gaps[0]
    )
    with pytest.raises(NotImplementedError, match="binding model"):
        package.function(n=1)


@pytest.mark.parametrize("build", [_read, _write])
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
        for hook in ("on_read", "on_update", "PropValue"):
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
