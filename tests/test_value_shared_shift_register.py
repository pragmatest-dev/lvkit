"""A value held in a shift register that a node changes in place: the copy rule
(``InMemoryVIGraph.value_is_shared``) must see the readers of the register's
start value outside the loop and of the register's value inside the loop.

Each case is a For Loop with count 1 and one shift register holding a 1D I32
array, generated and run. For the test an array counts as an object the call
changes in place (``is_by_value_object``)."""

from __future__ import annotations

import pytest

from lvkit.models import LVTypeKind
from tests.error_diagrams import (
    I32,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401
)

NAME = "Register.vi"

CHANGER = """
    from types import SimpleNamespace

    def change(x=None):
        x[0] = 99
        return SimpleNamespace(x_out=x)
"""

ARRAY_1D = (
    '<TypeDesc Type="Array" Format="inline">'
    '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
    f'<TypeDesc TypeID="{I32}" /></TypeDesc>'
)


@pytest.fixture(autouse=True)
def arrays_are_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    def by_value(lv_type: object) -> bool:
        return getattr(lv_type, "kind", None) == LVTypeKind.ARRAY

    monkeypatch.setattr("lvkit.codegen.value_copy.is_by_value_object", by_value)
    monkeypatch.setattr("lvkit.codegen.nodes.subvi.is_by_value_object", by_value)


def _diagram(
    project: Project,  # noqa: F811
    *,
    seen: bool,
    fork: bool,
) -> None:
    """``values`` -> the register's start; with ``seen`` it also goes to the
    indicator ``seen``. In the loop the register's value goes to change.vi; with
    ``fork`` it also goes round unchanged, otherwise change.vi's result does."""
    vi = Vi(NAME)
    array_type = vi.add_type(ARRAY_1D)
    values = vi.control("values", array_type, REQUIRED)
    after = vi.indicator("after", array_type)
    loop = vi.loop("forLoop", shift_registers=[array_type])
    register = loop.shift_registers[0]
    vi.wire(vi.int_constant(1), loop.selector)
    with vi.inside(loop.scopes[0]):
        change = vi.subvi(
            "change.vi", x=(0, array_type, False), x_out=(1, array_type, True)
        )
    vi.wire(values, register.left_outer)
    if seen:
        vi.wire(values, vi.indicator("seen", array_type))
    vi.wire(register.left_face, change["x"])
    if fork:
        vi.wire(register.left_face, register.right_face)
    else:
        vi.wire(change["x_out"], register.right_face)
    vi.wire(register.right_outer, after)
    project.add(vi)
    callee = Vi("change.vi")
    callee.control("x", array_type, REQUIRED)
    callee.indicator("x_out", array_type)
    callee.extra_types = vi.extra_types
    project.add(callee)


def test_a_start_value_also_read_outside_the_loop_is_copied(
    project: Project,  # noqa: F811
) -> None:
    _diagram(project, seen=True, fork=False)
    package = project.generate(NAME, {"change": CHANGER})
    out = package.function(values=[5, 6])
    assert (out.seen, out.after) == ([5, 6], [99, 6])


def test_a_register_value_that_also_goes_round_unchanged_is_copied(
    project: Project,  # noqa: F811
) -> None:
    _diagram(project, seen=False, fork=True)
    package = project.generate(NAME, {"change": CHANGER})
    out = package.function(values=[5, 6])
    assert out.after == [5, 6]


def test_a_register_value_nothing_else_reads_is_changed_where_it_is(
    project: Project,  # noqa: F811
) -> None:
    _diagram(project, seen=False, fork=False)
    package = project.generate(NAME, {"change": CHANGER})
    assert "copy.copy" not in package.source
    out = package.function(values=[5, 6])
    assert out.after == [99, 6]
