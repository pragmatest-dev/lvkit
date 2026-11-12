"""A value read from an array input stays in a variable when the array may be
changed in place before the value's reader runs (``codegen/output_binding.py``,
``_changed_before_read``). The changer here is a subVI whose stub changes its array
argument in place, as a node working on a value nothing else reads does. The node
under test is a catalog primitive written for the test: its output 0 is the first
element of its array input (``in_1[0]``), its output 2 passes the array on."""

from __future__ import annotations

import textwrap

import pytest

from lvkit.models import TerminalDirection
from lvkit.primitive_resolver import PrimitiveTerminal, ResolvedPrimitive, get_resolver
from tests.error_diagrams import (
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401
)

NAME = "Bind.vi"
FIRST_ELEMENT = 99901
IN, OUT = TerminalDirection.INPUT, TerminalDirection.OUTPUT

CHANGER = """
    from types import SimpleNamespace

    def change(x=None):
        x[0] = 99
        return SimpleNamespace(r=len(x))
"""


def _array(element: int) -> str:
    return (
        '<TypeDesc Type="Array" Format="inline">'
        '<Dimension Flags="0x00FF" FixedSize="0xFFFFFF" />'
        f'<TypeDesc TypeID="{element}" /></TypeDesc>'
    )


@pytest.fixture
def first_element(monkeypatch: pytest.MonkeyPatch) -> None:
    """The catalog answers ``FIRST_ELEMENT`` with the test primitive. The patch is
    on the catalog's class: patching the shared catalog object would leave its
    original method stored on it when undone, hiding a later test's class patch."""
    catalog = type(get_resolver())
    resolve = catalog.resolve

    def fake(
        self: object, prim_id: object = None, *args: object, **kwargs: object
    ) -> ResolvedPrimitive | None:
        if str(prim_id) != str(FIRST_ELEMENT):
            return resolve(self, prim_id, *args, **kwargs)  # type: ignore[arg-type]
        return ResolvedPrimitive(
            prim_id=str(FIRST_ELEMENT),
            name="First Element",
            confidence="high",
            python_code={"element": "in_1[0]", "array_out": "in_1"},
            terminals=[
                PrimitiveTerminal(index=0, direction=OUT, name="element"),
                PrimitiveTerminal(index=1, direction=IN, name="array"),
                PrimitiveTerminal(index=2, direction=OUT, name="array_out"),
            ],
        )

    monkeypatch.setattr(catalog, "resolve", fake)


def _body(package: Package) -> str:
    source = package.source
    body = source[source.index("def bind(") :].split("\n", 1)[1]
    return textwrap.dedent(body).strip()


def _diagram(project: Project, *, pass_on: bool, branch: bool) -> Vi:  # noqa: F811
    """``values`` -> First Element; its element -> indicator ``first``. With
    ``pass_on`` the array it passes on goes to the changer; with ``branch`` the
    ``values`` wire also goes to the changer itself."""
    vi = Vi(NAME)
    array_type = vi.add_type(_array(I32))
    values = vi.control("values", array_type, REQUIRED)
    first = vi.indicator("first", I32)
    node = vi.primitive(
        FIRST_ELEMENT,
        [
            ("element", 0, I32, True),
            ("array", 1, array_type, False),
            ("array out", 2, array_type, True),
        ],
    )
    vi.wire(values, node["array"])
    vi.wire(node["element"], first)
    if pass_on or branch:
        size = vi.indicator("size", I32)
        changer = vi.subvi(
            "change.vi", x=(0, array_type, False), r=(1, I32, True)
        )
        vi.wire(node["array out"] if pass_on else values, changer["x"])
        vi.wire(changer["r"], size)
    project.add(vi)
    callee = Vi("change.vi")
    callee.control("x", array_type, REQUIRED)
    callee.indicator("r", I32)
    callee.extra_types = vi.extra_types
    project.add(callee)
    return vi


def test_an_element_read_before_its_array_is_passed_on_keeps_the_old_value(
    project: Project,  # noqa: F811
    first_element: None,
) -> None:
    _diagram(project, pass_on=True, branch=False)
    package = project.generate(NAME, {"change": CHANGER})
    body = _body(package)
    assert "element = values[0]\n" in body, body
    out = package.function(values=[5, 6])
    assert (out.first, out.size) == (5, 2)


def test_an_element_read_of_a_wire_another_node_reads_keeps_the_old_value(
    project: Project,  # noqa: F811
    first_element: None,
) -> None:
    _diagram(project, pass_on=False, branch=True)
    package = project.generate(NAME, {"change": CHANGER})
    body = _body(package)
    assert body.startswith("element = values[0]\n"), body
    out = package.function(values=[5, 6])
    assert (out.first, out.size) == (5, 2)


def test_an_element_read_of_a_wire_nothing_else_reads_is_written_into_its_reader(
    project: Project,  # noqa: F811
    first_element: None,
) -> None:
    _diagram(project, pass_on=False, branch=False)
    package = project.generate(NAME)
    assert _body(package) == "return BindResult(first=values[0])"
    assert package.function(values=[5, 6]).first == 5
