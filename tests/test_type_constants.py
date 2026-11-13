"""The types a generated module records for its controls: the error cluster and the
scalar types are constants of the package's ``variants`` module, and any other
``LVType`` a module writes more than once is defined once at the top of it."""

from __future__ import annotations

import ast
from pathlib import Path

from lvkit.codegen.type_constants import share_type_constants
from lvkit.codegen.variants_module import lv_type_expression
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import LVType, LVTypeKind
from tests.class_graphs import INT, Project, Recipe, class_type, pane, wire
from tests.helpers import error_cluster_type

STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")


def _shared(source: str) -> str:
    return ast.unparse(share_type_constants(ast.parse(source)))


def test_an_error_cluster_is_the_error_constant_of_variants() -> None:
    expression = lv_type_expression(error_cluster_type())
    assert (expression.text, expression.names) == ("LV_Error", ("LV_Error",))


def test_a_scalar_is_a_constant_of_variants() -> None:
    assert lv_type_expression(INT).text == "LV_I32"
    assert lv_type_expression(STRING).text == "LV_String"


def test_a_type_written_twice_is_defined_once_after_the_imports() -> None:
    source = (
        "from .variants import LVType, Variant\n"
        "def f(x):\n"
        "    return Variant(x, LVType(112, 'Lib.lvlib:Event Source Actor.lvclass', "
        "None))\n"
        "def g(x):\n"
        "    return Variant(x, LVType(112, 'Lib.lvlib:Event Source Actor.lvclass', "
        "None))\n"
    )
    assert _shared(source).splitlines() == [
        "from .variants import LVType, Variant",
        "EVENT_SOURCE_ACTOR_TYPE = LVType(112, "
        "'Lib.lvlib:Event Source Actor.lvclass', None)",
        "",
        "def f(x):",
        "    return Variant(x, EVENT_SOURCE_ACTOR_TYPE)",
        "",
        "def g(x):",
        "    return Variant(x, EVENT_SOURCE_ACTOR_TYPE)",
    ]


def test_two_panels_sharing_a_class_type_define_it_once() -> None:
    source = (
        "from .panels import Panel\n"
        "from .variants import LVType\n"
        "a = Panel(f, types={'self': LVType(112, 'Thing.lvclass', None)})\n"
        "b = Panel(g, types={'self': LVType(112, 'Thing.lvclass', None)})\n"
    )
    result = _shared(source)
    assert result.count("LVType(112") == 1
    assert "THING_TYPE = LVType(112, 'Thing.lvclass', None)" in result
    assert "a = Panel(f, types={'self': THING_TYPE})" in result
    assert "b = Panel(g, types={'self': THING_TYPE})" in result


def test_a_type_written_once_stays_inline() -> None:
    source = "from .variants import LVType\nx = LVType(112, 'Thing.lvclass', None)\n"
    assert _shared(source) == source.strip()


def test_two_types_of_one_name_get_their_own_constants() -> None:
    source = (
        "from .variants import LVType\n"
        "a = [LVType(80, 'cluster{a}', None), LVType(80, 'cluster{a}', None)]\n"
        "b = [LVType(80, 'cluster{b}', None), LVType(80, 'cluster{b}', None)]\n"
    )
    result = _shared(source)
    assert "CLUSTER_TYPE = LVType(80, 'cluster{a}', None)" in result
    assert "CLUSTER_TYPE_2 = LVType(80, 'cluster{b}', None)" in result


def _class_and_string_vi(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI with a ``String.lvclass`` input wired to its output, and a plain
    String input."""
    owner = class_type("String")
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "c", owner, "c", 0),
                pane(vi, "input", "s", STRING, "s", 1),
            ],
            outputs=[pane(vi, "output", "d", owner, "d", 2)],
            data_flow=[wire(vi, "c", "d")],
        ),
        [],
    )


def test_a_class_named_string_does_not_take_the_name_of_the_string_constant(
    tmp_path: Path,
) -> None:
    recipe: Recipe = _class_and_string_vi
    project = Project(tmp_path)
    project.add_class("String", [])
    project.add_vi("Echo", recipe)
    package = project.generate()
    assert package.gaps == []
    source = package.sources["echo"]
    assert "from .string import String" in source
    assert "LV_String" in source
    with package.use() as module:
        string_class = module("string").String
        echo = module("echo").echo
        assert module("variants").LV_String.name == "String"
        mine = string_class()
        assert echo(c=mine, s="x").d is mine
        assert "'s': LV_String" in source
