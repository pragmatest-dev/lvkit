"""An Unbundle By Name output element that names no field (field index -1) and is
itself of type Void carries the node's input value, in a VI not saved broken
(issue #243: Merge Errors feeds the Unbundle By Name, whose element feeds an
And's error-cluster input; the And reads the error's status). In any other case
the element stays a gap. Each test builds a tiny diagram, generates its Python
and runs it."""

from __future__ import annotations

import pytest

from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "NoField.vi"
AND = 1061
EMPTY_CLUSTER = '<TypeDesc Type="Cluster" />'
VOID = '<TypeDesc Type="Void" />'


def _unbundle_no_field(vi: Vi, element_type: int, agg_type: int) -> dict[str, int]:
    """An Unbundle By Name whose aggregate is ``agg_type`` and whose one output
    element stores field index -1 and the type ``element_type``; terminals
    ``cluster`` and ``element``."""
    uid = vi.next_uid()
    agg = vi.next_uid(2)
    element = vi.next_uid(2)
    vi.scope.nodes.append(
        f'<SL__arrayElement class="nMux" uid="{uid}">'
        '<termList elements="2">'
        f'<SL__arrayElement class="term" uid="{agg}"><dco class="nmxDCO" '
        f'uid="{agg + 1}"><typeDesc>TypeID({agg_type})</typeDesc></dco>'
        "</SL__arrayElement>"
        f'<SL__arrayElement class="term" uid="{element}"><dco class="nmxDCO" '
        f'uid="{element + 1}"><objFlags>1</objFlags>'
        f"<typeDesc>TypeID({element_type})</typeDesc><i>-1</i></dco>"
        "</SL__arrayElement></termList>"
        f'<dcoList elements="1"><SL__arrayElement uid="{element + 1}" /></dcoList>'
        f'<dcoAgg uid="{agg + 1}" /></SL__arrayElement>'
    )
    return {"cluster": agg, "element": element}


def _error_into_and_through_unbundle(
    project: Project,  # noqa: F811
    *,
    element: str = "void",
    broken: bool = False,
) -> None:
    """result = And(x: Unbundle By Name(error in).element, y: True)."""
    vi = Vi(NAME)
    ein = vi.error_in()
    result = vi.indicator("result", BOOL)
    empty = vi.add_type(EMPTY_CLUSTER)
    element_type = vi.add_type(VOID) if element == "void" else I32
    unbundle = _unbundle_no_field(vi, element_type, empty)
    node = vi.primitive(
        AND,
        [("result", 0, BOOL, True), ("y", 1, BOOL, False), ("x", 2, ERR, False)],
    )
    vi.wire(ein, unbundle["cluster"])
    vi.wire(unbundle["element"], node["x"])
    vi.wire(vi.bool_constant(True), node["y"])
    vi.wire(node["result"], result)
    if broken:
        vi.execution = {"BadNode": "1"}
    project.add(vi)


def _error_in(package: Package, kind: str) -> object:
    if kind == "none":
        return None
    return package.error(5, status=kind == "error")


@pytest.mark.parametrize(
    ("error", "expected"), [("none", False), ("warning", False), ("error", True)]
)
def test_a_void_element_at_minus_one_carries_the_input_to_the_and(
    project: Project,  # noqa: F811
    error: str,
    expected: bool,
) -> None:
    _error_into_and_through_unbundle(project)
    package = project.generate(NAME)
    assert package.gaps == []
    result = package.function(error_in=_error_in(package, error))
    assert result.result is expected


def _reasons(package: Package) -> list[str]:
    return [str(gap) for gap in package.gaps]


def test_an_element_with_a_real_type_at_minus_one_is_still_not_decoded(
    project: Project,  # noqa: F811
) -> None:
    _error_into_and_through_unbundle(project, element="int")
    package = project.generate(NAME)
    assert any("which lvkit does not decode yet" in r for r in _reasons(package))


def test_the_same_diagram_saved_broken_still_selects_no_field(
    project: Project,  # noqa: F811
) -> None:
    _error_into_and_through_unbundle(project, broken=True)
    package = project.generate(NAME)
    assert any("selects no field of its cluster" in r for r in _reasons(package))
