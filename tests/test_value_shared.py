"""A node that changes a value in place works on a copy when something that does
not run before it can still read the value (``InMemoryVIGraph.value_is_shared``).
The order is the diagram's own: a path through the VI's own node (its controls
feed the diagram, its indicators are fed by it) or through a called VI's
definition is not one. Each case is a tiny diagram, generated and run.

The changer is a subVI whose stub changes its argument in place, the way a
method changes its object. For the test an array input counts as an object the
call changes in place (``is_by_value_object``)."""

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

NAME = "Share.vi"

CHANGER = """
    from types import SimpleNamespace

    def change(x=None, y=0):
        x[0] = 99
        return SimpleNamespace(r=y)
"""

PEEK = """
    from types import SimpleNamespace

    def peek(x=None):
        return SimpleNamespace(n=x[0])
"""


def _array(element: int) -> str:
    return (
        '<TypeDesc Type="Array" Format="inline">'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        f'<TypeDesc TypeID="{element}" /></TypeDesc>'
    )


@pytest.fixture(autouse=True)
def arrays_are_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    def by_value(lv_type: object) -> bool:
        return getattr(lv_type, "kind", None) == LVTypeKind.ARRAY

    monkeypatch.setattr("lvkit.codegen.value_copy.is_by_value_object", by_value)
    monkeypatch.setattr("lvkit.codegen.nodes.subvi.is_by_value_object", by_value)


def _callee(project: Project, name: str, array_type: int, extra: list[str]) -> None:  # noqa: F811
    callee = Vi(name)
    callee.control("x", array_type, REQUIRED)
    if name == "change.vi":
        callee.control("y", I32, OPTIONAL)
        callee.indicator("r", I32)
    else:
        callee.indicator("n", I32)
    callee.extra_types = extra
    project.add(callee)


def _diagram(project: Project, *, also_shown: bool, peek_first: bool) -> None:  # noqa: F811
    """``values`` -> change.vi (changes it in place); with ``also_shown`` the
    ``values`` wire also goes to the indicator ``seen``; with ``peek_first`` it
    goes to peek.vi, whose result change.vi waits for."""
    vi = Vi(NAME)
    array_type = vi.add_type(_array(I32))
    values = vi.control("values", array_type, REQUIRED)
    r = vi.indicator("r", I32)
    change = vi.subvi(
        "change.vi", x=(0, array_type, False), y=(1, I32, False), r=(2, I32, True)
    )
    vi.wire(values, change["x"])
    vi.wire(change["r"], r)
    if also_shown:
        vi.wire(values, vi.indicator("seen", array_type))
    if peek_first:
        peek = vi.subvi("peek.vi", x=(0, array_type, False), n=(1, I32, True))
        vi.wire(values, peek["x"])
        vi.wire(peek["n"], change["y"])
        _callee(project, "peek.vi", array_type, vi.extra_types)
    project.add(vi)
    _callee(project, "change.vi", array_type, vi.extra_types)


def test_a_value_an_indicator_also_shows_is_copied_before_it_is_changed(
    project: Project,  # noqa: F811
) -> None:
    _diagram(project, also_shown=True, peek_first=False)
    package = project.generate(NAME, {"change": CHANGER})
    assert "copy.deepcopy(values)" in package.source
    values = [5, 6]
    out = package.function(values=values)
    assert (out.seen, values) == ([5, 6], [5, 6])


def test_a_value_nothing_else_reads_is_changed_where_it_is(
    project: Project,  # noqa: F811
) -> None:
    _diagram(project, also_shown=False, peek_first=False)
    package = project.generate(NAME, {"change": CHANGER})
    assert "deepcopy" not in package.source


def test_a_reader_the_changer_waits_for_needs_no_copy(
    project: Project,  # noqa: F811
) -> None:
    _diagram(project, also_shown=False, peek_first=True)
    package = project.generate(NAME, {"change": CHANGER, "peek": PEEK})
    # peek.vi counts as a changer too and change.vi need not run after it, so
    # peek works on a copy; change.vi runs after peek, so it changes the value.
    assert "change(x=values, y=peek_result.n)" in package.source
    assert package.function(values=[5, 6]).r == 5
