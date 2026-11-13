"""An element taken out of an array with Index Array is still held by that array,
so a node that changes the element in place must work on a copy while the array
can still be read (``InMemoryVIGraph.value_is_shared``). In LabVIEW the array
keeps its old element.

The diagram: ``values`` (a two-dimensional array) -> Index Array (index 0, a row)
-> change.vi, whose stub changes the row in place; ``values`` also goes to the
indicator ``seen``. For the test an array counts as an object the call changes in
place (``is_by_value_object``)."""

from __future__ import annotations

import pytest

from lvkit.models import LVTypeKind
from tests.error_diagrams import (
    I32,
    OPTIONAL,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401
)

NAME = "Container.vi"

CHANGER = """
    from types import SimpleNamespace

    def change(x=None, y=0):
        x[0] = 99
        return SimpleNamespace(r=y)
"""

ARRAY_1D = (
    '<TypeDesc Type="Array" Format="inline">'
    '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
    f'<TypeDesc TypeID="{I32}" /></TypeDesc>'
)

ARRAY_2D = (
    '<TypeDesc Type="Array" Format="inline">'
    '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
    '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
    f'<TypeDesc TypeID="{I32}" /></TypeDesc>'
)


@pytest.fixture(autouse=True)
def arrays_are_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    def by_value(lv_type: object) -> bool:
        return getattr(lv_type, "kind", None) == LVTypeKind.ARRAY

    monkeypatch.setattr("lvkit.codegen.value_copy.is_by_value_object", by_value)
    monkeypatch.setattr("lvkit.codegen.nodes.subvi.is_by_value_object", by_value)


def _diagram(project: Project, *, also_shown: bool) -> None:  # noqa: F811
    vi = Vi(NAME)
    row_type = vi.add_type(ARRAY_1D)
    grid_type = vi.add_type(ARRAY_2D)
    values = vi.control("values", grid_type, REQUIRED)
    r = vi.indicator("r", I32)
    index = vi.index_array(grid_type, row_type)
    change = vi.subvi(
        "change.vi", x=(0, row_type, False), y=(1, I32, False), r=(2, I32, True)
    )
    vi.wire(values, index["array"])
    vi.wire(vi.int_constant(0), index["index"])
    vi.wire(index["element"], change["x"])
    vi.wire(change["r"], r)
    if also_shown:
        vi.wire(values, vi.indicator("seen", grid_type))
    project.add(vi)
    callee = Vi("change.vi")
    callee.control("x", row_type, REQUIRED)
    callee.control("y", I32, OPTIONAL)
    callee.indicator("r", I32)
    callee.extra_types = vi.extra_types
    project.add(callee)


def test_an_element_the_array_still_holds_is_copied_before_it_is_changed(
    project: Project,  # noqa: F811
) -> None:
    _diagram(project, also_shown=True)
    package = project.generate(NAME, {"change": CHANGER})
    values = [[5, 6]]
    out = package.function(values=values)
    assert "copy.copy(" in package.source
    assert (out.seen, values) == ([[5, 6]], [[5, 6]])


def test_an_element_is_copied_even_when_nothing_else_reads_the_array(
    project: Project,  # noqa: F811
) -> None:
    """Always copied, like a cluster field (a later optimisation may drop copies
    nothing needs)."""
    _diagram(project, also_shown=False)
    package = project.generate(NAME, {"change": CHANGER})
    assert "copy.copy(" in package.source


def test_a_row_an_auto_indexing_tunnel_gives_is_copied_before_it_is_changed(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    row_type = vi.add_type(ARRAY_1D)
    grid_type = vi.add_type(ARRAY_2D)
    values = vi.control("values", grid_type, REQUIRED)
    loop = vi.loop("forLoop", indexing_inputs=[(grid_type, row_type)])
    (row_in,) = loop.tunnels
    with vi.inside(loop.scopes[0]):
        change = vi.subvi(
            "change.vi", x=(0, row_type, False), y=(1, I32, False), r=(2, I32, True)
        )
    vi.wire(values, row_in.outer)
    vi.wire(row_in.faces[0], change["x"])
    vi.wire(values, vi.indicator("seen", grid_type))
    project.add(vi)
    callee = Vi("change.vi")
    callee.control("x", row_type, REQUIRED)
    callee.control("y", I32, OPTIONAL)
    callee.indicator("r", I32)
    callee.extra_types = vi.extra_types
    project.add(callee)
    package = project.generate(NAME, {"change": CHANGER})
    values = [[5, 6], [7, 8]]
    out = package.function(values=values)
    assert (out.seen, values) == ([[5, 6], [7, 8]], [[5, 6], [7, 8]])
