"""The connector pane is the interface of a function (issue #235): its Required
inputs are positional parameters, its Recommended and Optional inputs
keyword-only with their defaults, and its outputs are the fields of the one result
object. A front-panel control that is not on the pane is a local variable set to
its saved default (not a parameter); an indicator that is not on the pane is not
returned. A VI's function and a class's method follow the same rule."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

from lvkit.codegen.class_model import member_shape
from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import FPTerminal, LVType
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import (
    INT,
    Project,
    Recipe,
    add,
    call,
    class_type,
    pane,
    wire,
)

BASE, CHILD = class_type("Base"), class_type("Child")
REQUIRED = ParsedWiringRule.REQUIRED
RECOMMENDED = ParsedWiringRule.RECOMMENDED
OPTIONAL = ParsedWiringRule.OPTIONAL


def off_pane(
    vi: str, direction: str, tag: str, name: str, default: str | None = None
) -> FPTerminal:
    """A front-panel control or indicator that is not on the connector pane."""
    return FPTerminal(
        id=f"{vi}:{tag}",
        index=None,
        direction=direction,
        name=name,
        lv_type=INT,
        is_public=False,
        is_indicator=direction == "output",
        default_value=default,
    )


def with_default(terminal: FPTerminal, default: str) -> FPTerminal:
    return terminal.model_copy(update={"default_value": default})


def scale(owner: LVType, bonus: bool) -> Recipe:
    """A dynamic method: ``total`` = ``req`` + ``opt`` + two off-pane controls
    labelled ``Gain`` (5 and 7), or with ``bonus`` an own off-pane control
    ``Bonus`` (100) and no ``Gain``; an off-pane indicator ``Debug``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        controls = (
            [("bonus", "Bonus", "100")]
            if bonus
            else [("gain1", "Gain", "5"), ("gain2", "Gain", "7")]
        )
        extra = [off_pane(vi, "input", tag, *rest) for tag, *rest in controls]
        terms = [tag for tag, *_ in controls]
        wires = [wire(vi, "req", "s1.x"), wire(vi, "opt", "s1.y")]
        nodes: list[GraphNode] = [add(vi, "s1")]
        previous = "s1"
        for n, term in enumerate(terms, start=2):
            wires += [wire(vi, previous + ".sum", f"s{n}.x"), wire(vi, term, f"s{n}.y")]
            nodes.append(add(vi, f"s{n}"))
            previous = f"s{n}"
        wires += [wire(vi, previous + ".sum", "total"), wire(vi, "req", "debug")]
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "self", owner, "Self", 0),
                    pane(vi, "input", "req", INT, "req", 2, REQUIRED),
                    with_default(
                        pane(vi, "input", "opt", INT, "opt", 3, OPTIONAL), "10"
                    ),
                ],
                off_pane_inputs=extra,
                outputs=[pane(vi, "output", "total", INT, "total", 4)],
                off_pane_outputs=[off_pane(vi, "output", "debug", "Debug")],
                data_flow=wires,
            ),
            nodes,
        )

    return build


def make(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A static method with no class wire: ``value`` = ``seed`` + two off-pane
    controls labelled ``k`` (2 and 3)."""
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "seed", INT, "seed", 0, REQUIRED)],
            off_pane_inputs=[
                off_pane(vi, "input", "k1", "k", "2"),
                off_pane(vi, "input", "k2", "k", "3"),
            ],
            outputs=[pane(vi, "output", "value", INT, "value", 1)],
            data_flow=[
                wire(vi, "seed", "s1.x"),
                wire(vi, "k1", "s1.y"),
                wire(vi, "s1.sum", "s2.x"),
                wire(vi, "k2", "s2.y"),
                wire(vi, "s2.sum", "value"),
            ],
        ),
        [add(vi, "s1"), add(vi, "s2")],
    )


def mix(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI of no class: ``a`` Required, ``b`` Recommended, ``c`` Optional (4),
    an off-pane control ``Offset`` (9) and an off-pane indicator ``Dbg``;
    ``out`` = ``a`` + ``b`` + ``c`` + ``Offset``."""
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "a", INT, "a", 0, REQUIRED),
                pane(vi, "input", "b", INT, "b", 1, RECOMMENDED),
                with_default(pane(vi, "input", "c", INT, "c", 2, OPTIONAL), "4"),
            ],
            off_pane_inputs=[off_pane(vi, "input", "offset", "Offset", "9")],
            outputs=[pane(vi, "output", "out", INT, "out", 3)],
            off_pane_outputs=[off_pane(vi, "output", "dbg", "Dbg")],
            data_flow=[
                wire(vi, "a", "s1.x"),
                wire(vi, "b", "s1.y"),
                wire(vi, "s1.sum", "s2.x"),
                wire(vi, "c", "s2.y"),
                wire(vi, "s2.sum", "s3.x"),
                wire(vi, "offset", "s3.y"),
                wire(vi, "s3.sum", "out"),
                wire(vi, "a", "dbg"),
            ],
        ),
        [add(vi, "s1"), add(vi, "s2"), add(vi, "s3")],
    )


def lone(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI whose controls and indicators are all off the connector pane."""
    return (
        VIContext(
            name=vi,
            off_pane_inputs=[off_pane(vi, "input", "x", "x", "3")],
            off_pane_outputs=[off_pane(vi, "output", "y", "y")],
            data_flow=[wire(vi, "x", "y")],
        ),
        [],
    )


def shapes_project(tmp_path: Path) -> Project:
    project = Project(tmp_path)
    project.add_class("Base", [])
    project.add_class("Child", [], parent="Base")
    project.add_method("Base", "Scale", scale(BASE, bonus=False))
    project.add_method("Child", "Scale", scale(CHILD, bonus=True))
    project.add_method("Base", "Make", make, static=True)
    return project


def test_a_dynamic_method_takes_required_then_keyword_only(tmp_path: Path) -> None:
    package = shapes_project(tmp_path).generate()
    assert package.gaps == []
    source = package.sources["base"]
    assert "def scale(self, req: int, *, opt: int=10) -> ScaleResult:" in source
    assert "class ScaleResult(NamedTuple):\n    total: int" in source
    # The off-pane controls are locals in front-panel order, each its own name,
    # and the docstring lists every off-pane control and indicator.
    assert "gain = 5\n" in source and "gain_2 = 7\n" in source
    assert "Front panel, not on the connector pane:" in source
    assert "    gain: int = 5\n    gain_2: int = 7\n" in source
    assert (
        "Indicators (written by the diagram, not returned):\n    debug: int" in source
    )
    with package.use() as module:
        base = module("base").Base()
        result = base.scale(1)
        assert result == (23,) and result.total == 23
        assert base.scale(req=1, opt=0).total == 13
        with pytest.raises(TypeError):
            base.scale(1, 2)
        with pytest.raises(TypeError):
            base.scale()
        with pytest.raises(TypeError):
            base.scale(req=1, gain=0)


def test_an_override_has_the_declarations_signature_and_its_own_controls(
    tmp_path: Path,
) -> None:
    project = shapes_project(tmp_path)
    package = project.generate()
    assert package.gaps == []
    declared = member_shape(project.graph, project.vis["Base.Scale"])
    override = member_shape(project.graph, project.vis["Child.Scale"])
    assert declared is not None and override is not None
    assert override.parameters == declared.parameters == {2: "req", 3: "opt"}
    assert override.required == declared.required == frozenset({2})
    assert override.result_class == declared.result_class == "ScaleResult"
    source = package.sources["child"]
    assert "def scale(self, req: int, *, opt: int=10) -> ScaleResult:" in source
    assert "bonus: int = 100" in source
    assert "class ScaleResult" not in source
    with package.use() as module:
        child = module("child").Child()
        assert child.scale(1).total == 1 + 10 + 100
        assert type(child.scale(1)) is type(module("base").Base().scale(1))


def test_a_static_method_with_two_off_pane_controls(tmp_path: Path) -> None:
    package = shapes_project(tmp_path).generate()
    source = package.sources["base"]
    assert "@staticmethod\n    def make(seed: int) -> MakeResult:" in source
    assert "    k: int = 2\n    k_2: int = 3\n" in source
    with package.use() as module:
        base = module("base").Base
        assert base.make(seed=1).value == 6
        assert base.make(1).value == 6
        with pytest.raises(TypeError):
            base.make(seed=1, k=5)


def test_a_vi_has_required_positional_and_the_rest_keyword_only(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_vi("Mix", mix)
    package = project.generate()
    source = package.sources["mix"]
    assert "def mix(a: int, *, b: int=0, c: int=4) -> MixResult:" in source
    assert "class MixResult(NamedTuple):\n    out: int" in source
    assert "Offset" not in source.split('"""')[0]
    with package.use() as module:
        function = module("mix").mix
        assert function(1).out == 1 + 0 + 4 + 9
        assert function(a=1, b=2, c=3).out == 15
        assert list(module("mix").MixResult._fields) == ["out"]
        parameters = inspect.signature(function).parameters
        assert [p.kind.name for p in parameters.values()] == [
            "POSITIONAL_OR_KEYWORD",
            "KEYWORD_ONLY",
            "KEYWORD_ONLY",
        ]


def test_a_vi_with_everything_off_the_pane_has_no_parameter_and_no_result(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_vi("Lone", lone)
    package = project.generate()
    source = package.sources["lone"]
    assert "def lone():" in source
    assert "    x: int = 3\n" in source and "    y: int\n" in source
    with package.use() as module:
        assert module("lone").lone() is None


def caller(scale_key: str, mix_key: str, wire_a: bool = True) -> Recipe:
    """A VI that calls ``mix`` (with ``a`` only), then ``scale`` on its object
    with ``mix``'s result, and returns ``scale``'s total."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        wires = [
            wire(vi, "mix.out", "scale.req"),
            wire(vi, "obj", "scale.self"),
            wire(vi, "scale.total", "total"),
        ]
        if wire_a:
            wires.append(wire(vi, "x", "mix.a"))
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "obj", BASE, "obj", 0),
                    pane(vi, "input", "x", INT, "x", 1),
                ],
                outputs=[pane(vi, "output", "total", INT, "total", 2)],
                data_flow=wires,
            ),
            [
                call(
                    vi,
                    "mix",
                    mix_key,
                    "iUse",
                    [("a", 0, INT), ("b", 1, INT), ("c", 2, INT)],
                    [("out", 3, INT)],
                ),
                call(
                    vi,
                    "scale",
                    scale_key,
                    "dynIUse",
                    [("self", 0, BASE), ("req", 2, INT), ("opt", 3, INT)],
                    [("total", 4, INT)],
                ),
            ],
        )

    return build


def test_a_caller_reads_the_results_of_a_vi_and_a_method_by_field(
    tmp_path: Path,
) -> None:
    project = shapes_project(tmp_path)
    mix_key = project.add_vi("Mix", mix)
    project.add_vi("Caller", caller(project.vis["Base.Scale"], mix_key))
    package = project.generate()
    assert package.gaps == []
    source = package.sources["caller"]
    assert "mix(a=x)" in source and ".scale(req=" in source
    assert "mix_result.out" in source and "scale_result.total" in source
    with package.use() as module:
        obj = module("base").Base()
        assert module("caller").caller(obj=obj, x=1).total == 14 + 10 + 5 + 7


def test_a_call_that_leaves_a_required_input_unwired_is_a_gap(tmp_path: Path) -> None:
    project = shapes_project(tmp_path)
    mix_key = project.add_vi("Mix", mix)
    project.add_vi("Caller", caller(project.vis["Base.Scale"], mix_key, wire_a=False))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert [g.reason for g in gaps] == [
        "The call of 'Mix.vi' leaves its required input 'a' unwired"
    ]
    with package.use() as module:
        with pytest.raises(NotImplementedError, match="required input 'a' unwired"):
            module("caller").caller(obj=module("base").Base(), x=1)


def test_a_lazy_import_goes_after_the_docstring() -> None:
    import ast

    from lvkit.codegen.class_builder.methods import _with_imports_after_docstring

    imports = ast.parse("import os").body
    documented = ast.parse('"""Doc."""\nx = 1').body
    assert ast.unparse(_with_imports_after_docstring(imports, documented)) == (
        "'Doc.'\nimport os\nx = 1"
    )
    plain = ast.parse("x = 1").body
    assert ast.unparse(_with_imports_after_docstring(imports, plain)) == (
        "import os\nx = 1"
    )


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a folder name cannot hold ':' and '\\' there; Windows runs every test "
    "under its own such path",
)
def test_a_project_under_a_windows_style_path_generates_the_same(
    tmp_path: Path,
) -> None:
    """Ids hold the file path, which on Windows has ``:`` and ``\\``; nothing is
    named from an id, so the project generates as under any other path."""
    root = tmp_path / "C:\\Users\\x"
    root.mkdir()
    project = shapes_project(root)
    mix_key = project.add_vi("Mix", mix)
    project.add_vi("Caller", caller(project.vis["Base.Scale"], mix_key))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        obj = module("base").Base()
        assert module("caller").caller(obj=obj, x=1).total == 14 + 10 + 5 + 7


def test_a_repeated_output_name_is_numbered_never_named_by_the_node_id() -> None:
    from lvkit.codegen.context import CodeGenContext

    ctx = CodeGenContext(vi_name="t.vi")
    node_id = "C:\\Users\\x\\Scale.vi:s3"
    names = [ctx.make_output_var("total", node_id) for _ in range(3)]
    assert names == ["total", "total_2", "total_3"]
