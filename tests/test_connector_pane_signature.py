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
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, LocalVariableNode, LoopNode, VIContext
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.parser.models import ParsedWiringRule
from lvkit.pipeline import create_import_resolver
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
from tests.vi_reference_diagrams import this_vi_type

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


def counter(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A While Loop that stops when the Required input ``stop`` is True; its
    iteration terminal ``i`` is wired only to the off-pane indicator ``Values``."""
    boolean = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
    loop = LoopNode(
        id=f"{vi}:loop",
        vi_path=vi,
        name="While Loop",
        node_type="whileLoop",
        loop_type="whileLoop",
        stop_condition_terminal=f"{vi}:loop.stop",
        terminals=[
            Terminal(id=f"{vi}:loop.i", index=0, direction="output", lv_type=INT),
            Terminal(
                id=f"{vi}:loop.stop", index=1, direction="input", lv_type=boolean
            ),
        ],
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "stop", boolean, "stop", 0)],
            off_pane_outputs=[off_pane(vi, "output", "values", "Values")],
            data_flow=[wire(vi, "loop.i", "values"), wire(vi, "stop", "loop.stop")],
        ),
        [loop],
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


def test_a_counter_read_only_by_an_off_pane_indicator_keeps_its_start(
    tmp_path: Path,
) -> None:
    """The While Loop's ``i`` steps with ``i += 1``, which reads ``i``: its
    starting value stays although no returned value reads it."""
    project = Project(tmp_path)
    project.add_vi("Counter", counter)
    package = project.generate()
    assert package.gaps == []
    source = package.sources["counter"]
    assert "    i = 0\n    while True:" in source
    assert "        i += 1" in source
    with package.use() as module:
        assert module("counter").counter(True) is None


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


def test_an_override_whose_declaration_was_not_loaded_is_called_with_its_inputs(
    tmp_path: Path,
) -> None:
    """The parent class lists ``Scale.vi`` but its VI was not loaded: the
    child's override is the declaration Python sees, so a call passes the
    inputs it wires by the override's own parameter names."""
    project = Project(tmp_path)
    base = project.add_class("Base", [])
    project.add_class("Child", [], parent="Base")
    project.graph._dep_graph.nodes[base]["method_paths"]["Scale.vi"] = str(  # noqa: SLF001
        tmp_path / "src" / "Base" / "Scale.vi"
    )
    scale_key = project.add_method("Child", "Scale", scale(CHILD, bonus=True))

    def run(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "obj", CHILD, "obj", 0),
                    pane(vi, "input", "x", INT, "x", 1),
                ],
                outputs=[pane(vi, "output", "total", INT, "total", 2)],
                data_flow=[
                    wire(vi, "obj", "scale.self"),
                    wire(vi, "x", "scale.req"),
                    wire(vi, "scale.total", "total"),
                ],
            ),
            [
                call(
                    vi,
                    "scale",
                    scale_key,
                    "dynIUse",
                    [("self", 0, CHILD), ("req", 2, INT), ("opt", 3, INT)],
                    [("total", 4, INT)],
                )
            ],
        )

    project.add_vi("Run", run)
    package = project.generate()
    assert package.gaps == []
    assert "def scale(self, req: int, *, opt: int=10) -> ScaleResult:" in (
        package.sources["child"]
    )
    assert ".scale(req=x)" in package.sources["run"]
    with package.use() as module:
        child = module("child").Child()
        assert module("run").run(child, 1).total == 1 + 10 + 100


def itself(receiver: LVType | None) -> Recipe:
    """A method whose "This VI" reference is its output ``ref``; with
    ``receiver`` it is a dynamic method taking that class wire."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        ref_type = this_vi_type()
        this_vi = LocalVariableNode(
            id=f"{vi}:this",
            vi_path=vi,
            name="This VI",
            terminals=[
                Terminal(
                    id=f"{vi}:this.ref", index=0, direction="output", lv_type=ref_type
                )
            ],
        )
        inputs = [pane(vi, "input", "self", receiver, "Self", 0)] if receiver else []
        return (
            VIContext(
                name=vi,
                inputs=inputs,
                outputs=[pane(vi, "output", "ref", ref_type, "ref", 1)],
                data_flow=[wire(vi, "this.ref", "ref")],
            ),
            [this_vi],
        )

    return build


def test_this_vi_in_a_method_is_the_method_on_its_class(tmp_path: Path) -> None:
    """Inside a class the bare method name is not defined: "This VI" is the
    method on its class, for a static and a dynamic method alike."""
    project = Project(tmp_path)
    project.add_class("Base", [])
    project.add_method("Base", "Find", itself(None), static=True)
    project.add_method("Base", "Self Ref", itself(BASE))
    package = project.generate()
    assert package.gaps == []
    source = package.sources["base"]
    assert "return FindResult(ref=Base.find)" in source
    assert "return SelfRefResult(ref=Base.self_ref)" in source
    with package.use() as module:
        base = module("base").Base
        assert base.find().ref is base.find
        assert base.find().ref.__name__ == "find"
        assert base().self_ref().ref is base.self_ref


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

    from lvkit.codegen.import_cycles import with_imports_after_docstring

    imports = ast.parse("import os").body
    documented = ast.parse('"""Doc."""\nx = 1').body
    assert ast.unparse(with_imports_after_docstring(imports, documented)) == (
        "'Doc.'\nimport os\nx = 1"
    )
    plain = ast.parse("x = 1").body
    assert ast.unparse(with_imports_after_docstring(imports, plain)) == (
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


def named_like_mix(mix_key: str, on_pane: bool) -> Recipe:
    """A VI whose control is labelled ``mix`` (6), on the connector pane or
    off it, wired to ``a`` of a call to ``Mix.vi``, whose function is ``mix``;
    it returns the call's ``out``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        control = (
            pane(vi, "input", "m", INT, "mix", 0)
            if on_pane
            else off_pane(vi, "input", "m", "mix", "6")
        )
        return (
            VIContext(
                name=vi,
                inputs=[control] if on_pane else [],
                off_pane_inputs=[] if on_pane else [control],
                outputs=[pane(vi, "output", "total", INT, "total", 1)],
                data_flow=[wire(vi, "m", "mix.a"), wire(vi, "mix.out", "total")],
            ),
            [
                call(
                    vi,
                    "mix",
                    mix_key,
                    "iUse",
                    [("a", 0, INT), ("b", 1, INT), ("c", 2, INT)],
                    [("out", 3, INT)],
                )
            ],
        )

    return build


@pytest.mark.parametrize("on_pane", [False, True], ids=["off-pane", "on-pane"])
def test_a_control_named_like_a_called_vi_leaves_the_call_to_the_vi(
    tmp_path: Path, on_pane: bool
) -> None:
    """A name a Python function assigns is its local everywhere in it: the
    function of the called VI is imported under a name of its own, so the call
    reaches the VI and the control keeps its name."""
    project = Project(tmp_path)
    mix_key = project.add_vi("Mix", mix)
    project.add_vi("Caller", named_like_mix(mix_key, on_pane))
    package = project.generate()
    assert package.gaps == []
    source = package.sources["caller"]
    assert "from .mix import mix as mix_2\n" in source
    assert "mix_2(a=mix)" in source
    with package.use() as module:
        caller_fn = module("caller").caller
        result = caller_fn(6) if on_pane else caller_fn()
        assert result.total == 6 + 0 + 4 + 9


def test_a_variable_named_after_a_call_never_takes_the_called_function() -> None:
    """Once a function calls ``mix``, a value it names ``mix`` later is
    ``mix_2``, and a second call to the VI reuses the first import."""
    ctx = CodeGenContext()
    assert ctx.import_function("Mix.vi") == "mix"
    assert ctx.make_output_var("mix", "node") == "mix_2"
    assert ctx.import_function("Mix.vi") == "mix"
    assert ctx.imports == {"from .mix import mix"}


def test_the_alias_is_never_a_top_level_name_of_the_module(tmp_path: Path) -> None:
    """The caller is ``Mix_2.vi``, so ``mix_2`` is its own function: the
    called ``mix`` is imported as ``mix_3``, not as the caller itself."""
    project = Project(tmp_path)
    mix_key = project.add_vi("Mix", mix)
    project.add_vi("Mix_2", named_like_mix(mix_key, on_pane=True))
    package = project.generate()
    source = package.sources["mix_2"]
    assert "from .mix import mix as mix_3\n" in source
    assert "def mix_2(mix: int)" in source and "mix_3(a=mix)" in source
    with package.use() as module:
        assert module("mix_2").mix_2(6).total == 6 + 0 + 4 + 9


def test_a_name_bound_only_on_the_graph_is_held() -> None:
    """A For Loop's element variable or a method's parameter is bound on a
    terminal, not counted among the made variables: it still holds its name."""
    ctx = CodeGenContext.from_wires([], bindings={"element": "mix"})
    assert ctx.import_function("Mix.vi") == "mix_2"
    assert ctx.imports == {"from .mix import mix as mix_2"}


def test_resolvers_of_one_module_share_its_names(tmp_path: Path) -> None:
    """The variants of a polymorphic VI share one module: a sibling's function
    name (``mix_2``) is never an alias, and an alias one variant takes is not
    given to another."""
    names = {"mix_2"}
    out = tmp_path / "pkg"
    first, second = (
        create_import_resolver(
            "pkg", out, {}, out / "poly.py", module_names=names
        )
        for _ in range(2)
    )
    assert first.alias_name("mix", lambda n: False) == "mix_3"
    assert second.alias_name("mix", lambda n: False) == "mix_4"


def calls_itself(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """``Again.vi``: an off-pane control labelled ``again`` (1) wired to ``a``
    of a call to ``Again.vi`` itself."""
    control = off_pane(vi, "input", "c", "again", "1")
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "a", INT, "a", 0, OPTIONAL)],
            off_pane_inputs=[control],
            outputs=[pane(vi, "output", "out", INT, "out", 1)],
            data_flow=[wire(vi, "c", "self.a"), wire(vi, "self.out", "out")],
        ),
        [call(vi, "self", vi, "iUse", [("a", 0, INT)], [("out", 1, INT)])],
    )


def test_a_vi_that_calls_itself_while_a_control_has_its_name(tmp_path: Path) -> None:
    """The call reaches the function, not the control: the function imports its
    own module's function under a name of its own, inside its body."""
    project = Project(tmp_path)
    project.add_vi("Again", calls_itself)
    package = project.generate()
    source = package.sources["again"]
    assert "\n    from .again import again as again_2\n" in source
    assert "    again = 1\n" in source and "again_2(a=again)" in source
    with package.use() as module:
        assert callable(module("again").again)
