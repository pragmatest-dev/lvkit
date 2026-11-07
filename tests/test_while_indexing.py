"""A While Loop's auto-indexing input tunnel, and a Not a Refnum constant, on
tiny handmade diagrams, through every view and the generated Python."""

from __future__ import annotations

import pytest

from tests.error_diagrams import (
    BOOL,
    FILE,
    I32,
    REQUIRED,
    Project,
    Vi,
    json_body,
    project,  # noqa: F401  (the fixture)
)

NAME = "Indexing.vi"


def _array(element: int) -> str:
    return (
        '<TypeDesc Type="Array" Format="inline">'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        f'<TypeDesc TypeID="{element}" /></TypeDesc>'
    )


def _while_indexing(project: Project) -> str:  # noqa: F811
    """A While Loop with two auto-indexing input tunnels: ``values`` (I32) feeds
    a last-value output tunnel to ``last``, ``stops`` (Boolean) feeds the stop
    terminal."""
    vi = Vi(NAME)
    values_type = vi.add_type(_array(I32))
    stops_type = vi.add_type(_array(BOOL))
    values = vi.control("values", values_type, REQUIRED)
    stops = vi.control("stops", stops_type, REQUIRED)
    last = vi.indicator("last", I32)
    loop = vi.loop(
        "whileLoop",
        tunnels=[(I32, True)],
        indexing_inputs=[(values_type, I32), (stops_type, BOOL)],
    )
    out, value_in, stop_in = loop.tunnels
    vi.wire(values, value_in.outer)
    vi.wire(stops, stop_in.outer)
    vi.wire(value_in.faces[0], out.faces[0])
    vi.wire(stop_in.faces[0], loop.faces[0])
    vi.wire(out.outer, last)
    project.add(vi)
    return NAME


@pytest.mark.parametrize(
    ("values", "stops", "last"),
    [
        ([10, 20, 30, 40], [False, False, True], 30),
        # past the end of an array the tunnel gives the element type's default
        ([10, 20], [False, False, False, True], 0),
    ],
)
def test_a_while_loop_indexes_an_auto_indexing_input_by_its_iteration(
    project: Project,  # noqa: F811
    values: list[int],
    stops: list[bool],
    last: int,
) -> None:
    package = project.generate(_while_indexing(project))
    assert package.function(values=values, stops=stops).last == last


def test_the_views_show_the_auto_indexing_input_tunnels(
    project: Project,  # noqa: F811
) -> None:
    """describe names the indexing tunnels. lvnet shows the whole array on the
    input side: its input-side marker is open in the lvnet design
    (``netlist-language.md`` section 17, item 3)."""
    views = project.views(_while_indexing(project))
    assert (
        "While Loop\n  Tunnels:\n    in  indexing tunnel (values)\n"
        "    in  indexing tunnel (stops)\n    out last value tunnel (last)"
    ) in views.describe
    assert (
        "      loop_109::condition = stops\n"
        "      tunnel loop_109::out0 : last-value = values\n"
    ) in views.lvnet
    assert json_body(views)


def test_a_not_a_refnum_constant_is_none(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    refnum = vi.indicator("refnum", FILE)
    vi.wire(vi.constant(FILE, "00000000"), refnum)
    project.add(vi)
    package = project.generate(NAME)
    assert package.function().refnum is None
    assert "'None'" not in package.source


def test_a_refnum_constant_with_a_saved_handle_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    refnum = vi.indicator("refnum", FILE)
    vi.wire(vi.constant(FILE, "00000005"), refnum)
    project.add(vi)
    package = project.generate(NAME)
    with pytest.raises(NotImplementedError, match="holds the handle Refnum"):
        package.function()
