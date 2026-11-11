"""A VI's front panel by reference (``codegen/nodes/ctrl_val.py``): Ctrl Val.Set,
Get and Get All and Run VI through ``front_panels.FrontPanel``. Each case is a
handmade diagram whose generated package is imported and run."""

from __future__ import annotations

import threading
from pathlib import Path
from types import ModuleType

from lvkit.codegen.front_panels_module import FRONT_PANELS_MODULE_SOURCE
from lvkit.codegen.gaps import Gap
from lvkit.graph.models import ConstantNode, GraphNode, PrimitiveNode, VIContext
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    Terminal,
)
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
from tests.test_run_vi import project_with as run_vi_project

BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
VI_REF = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="VIRefnum")
STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
VARIANT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="LVVariant")
ENTRY = LVType(
    kind=LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[
        ClusterField(name="name", type=STRING),
        ClusterField(name="value", type=VARIANT),
    ],
)
ENTRIES = LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=ENTRY)
ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    typedef_name="error",
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=INT),
        ClusterField(name="source", type=STRING),
    ],
)

SET, GET, GET_ALL, RUN = 1051, 1052, 1053, 1003


def callee(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI with the control ``x`` and the indicator ``y``: ``y`` is ``x + 1``."""
    one = ConstantNode(
        id=f"{vi}:one",
        vi_path=vi,
        name="one",
        value="1",
        lv_type=INT,
        terminals=[Terminal(id=f"{vi}:one", index=0, direction="output", lv_type=INT)],
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "x", INT, "x", 0, ParsedWiringRule.RECOMMENDED)],
            outputs=[pane(vi, "output", "y", INT, "y", 1)],
            data_flow=[
                wire(vi, "x", "add.x"),
                wire(vi, "one", "add.y"),
                wire(vi, "add.sum", "y"),
            ],
        ),
        [one, add(vi, "add")],
    )


def invoke(vi: str, tag: str, method: str, code: int) -> PrimitiveNode:
    """An Invoke Node on the VI class: reference in 0 and out 1, error in 2 and
    out 3, a result at 5, parameters at 6 and 8."""
    terminals = [
        ("ref", 0, "input", VI_REF),
        ("ref_out", 1, "output", VI_REF),
        ("err_in", 2, "input", ERROR),
        ("err_out", 3, "output", ERROR),
        ("result", 5, "output", ENTRIES if code == GET_ALL else VARIANT),
        ("param", 6, "input", BOOL if code in (GET_ALL, RUN) else STRING),
        ("value", 8, "input", VARIANT),
    ]
    return PrimitiveNode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name="Invoke Node",
        node_type="invokeNode",
        object_name="VI",
        method_name=method,
        method_code=code,
        terminals=[
            Terminal(id=f"{vi}:{tag}.{name}", index=index, direction=d, lv_type=t)
            for name, index, d, t in terminals
        ],
    )


def constant(vi: str, tag: str, value: str, lv_type: LVType) -> ConstantNode:
    return ConstantNode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name=tag,
        value=value,
        lv_type=lv_type,
        terminals=[
            Terminal(id=f"{vi}:{tag}", index=0, direction="output", lv_type=lv_type)
        ],
    )


def static_reference(vi: str, key: str) -> ConstantNode:
    node = constant(vi, "ref", "Target.vi", VI_REF)
    node.referenced_vi_key = key
    return node


class Diagram:
    """The nodes and wires of one VI, built up a step at a time."""

    def __init__(self, vi: str) -> None:
        self.vi = vi
        self.nodes: list[GraphNode] = []
        self.wires = []

    def wire(self, source: str, target: str) -> None:
        self.wires.append(wire(self.vi, source, target))

    def step(
        self,
        tag: str,
        method: str,
        code: int,
        name: str | None = None,
        value: tuple[str, LVType] | None = None,
        wait: str | None = None,
    ) -> None:
        """An Invoke Node of ``method``; ``name`` wires a String constant to its
        first parameter, ``value`` a constant to its second, ``wait`` a Boolean
        constant to Run VI's Wait Until Done."""
        self.nodes.append(invoke(self.vi, tag, method, code))
        if name is not None:
            self.nodes.append(constant(self.vi, f"{tag}_name", name, STRING))
            self.wire(f"{tag}_name", f"{tag}.param")
        if value is not None:
            self.nodes.append(constant(self.vi, f"{tag}_value", *value))
            self.wire(f"{tag}_value", f"{tag}.value")
        if wait is not None:
            self.nodes.append(constant(self.vi, f"{tag}_wait", wait, BOOL))
            self.wire(f"{tag}_wait", f"{tag}.param")


def chained(target_key: str, steps: list[tuple], reads: str | None) -> Recipe:
    """A VI with a Static VI Reference to ``target_key`` wired into each of
    ``steps`` in turn through the error wire (``Diagram.step`` arguments). Its
    output ``out`` is the result of the last step when ``reads`` is ``"value"``,
    ``"entries"``; its ``err`` is the last step's error out."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        d = Diagram(vi)
        d.nodes.append(static_reference(vi, target_key))
        previous = None
        for index, (method, code, kwargs) in enumerate(steps):
            tag = f"s{index}"
            d.step(tag, method, code, **kwargs)
            d.wire("ref", f"{tag}.ref")
            if previous is not None:
                d.wire(f"{previous}.err_out", f"{tag}.err_in")
            previous = tag
        outputs = [pane(vi, "output", "err", ERROR, "error out", 1)]
        d.wire(f"{previous}.err_out", "err")
        if reads is not None:
            outputs.append(
                pane(
                    vi,
                    "output",
                    "out",
                    ENTRIES if reads == "entries" else VARIANT,
                    "out",
                    0,
                )
            )
            d.wire(f"{previous}.result", "out")
        return VIContext(name=vi, outputs=outputs, data_flow=d.wires), d.nodes

    return build


def project_with(tmp_path: Path, steps: list[tuple], reads: str | None) -> Project:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", callee)
    runner_key = project.add_vi("Runner", chained(target_key, steps, reads))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    return project


def step(method: str, code: int, **kwargs: object) -> tuple:
    return (method, code, kwargs)


def test_set_run_get_through_a_static_reference(tmp_path: Path) -> None:
    steps = [
        step("Ctrl Val.Set", SET, name="x", value=("4", INT)),
        step("Run VI", RUN),
        step("Ctrl Val.Get", GET, name="y"),
    ]
    gaps: list[Gap] = []
    package = project_with(tmp_path, steps, "value").generate(gaps)
    assert gaps == []
    source = package.sources["runner"]
    assert "FrontPanel.of(" in source
    assert "target()" not in source
    with package.use() as module:
        result = module("runner").runner()
        assert result.out == 5
        assert result.error_out is None


def test_set_and_run_with_the_reference_wired_in_parallel_use_the_front_panel(
    tmp_path: Path,
) -> None:
    """A Run VI on a reference the diagram also sets controls on is a run of the
    front panel, not a call with no arguments."""
    steps = [
        step("Ctrl Val.Set", SET, name="x", value=("4", INT)),
        step("Run VI", RUN),
    ]
    package = project_with(tmp_path, steps, None).generate()
    with package.use() as module:
        runner = module("runner")
        runner.runner()
        panel = module("front_panels").FrontPanel.of(module("target").target)
        assert panel.get("y") == 5
        assert panel.get("x") == 4


def test_get_all_lists_the_controls_or_the_indicators(tmp_path: Path) -> None:
    both = [
        step("Ctrl Val.Set", SET, name="x", value=("4", INT)),
        step("Run VI", RUN),
        step("Ctrl Val.Get All", GET_ALL),
    ]
    package = project_with(tmp_path, both, "entries").generate()
    with package.use() as module:
        assert module("runner").runner().out == [("y", 5)]
        panel = module("front_panels").FrontPanel.of(module("target").target)
        assert panel.get_all(True) == [("x", 4)]
        assert panel.get_all(False) == [("y", 5)]


def test_unknown_control_name_is_error_1100_and_nothing_runs(tmp_path: Path) -> None:
    steps = [
        step("Ctrl Val.Set", SET, name="nope", value=("4", INT)),
        step("Run VI", RUN),
        step("Ctrl Val.Get", GET, name="y"),
    ]
    package = project_with(tmp_path, steps, "value").generate()
    with package.use() as module:
        result = module("runner").runner()
        assert result.error_out.code == 1100
        assert result.error_out.message == "No object of that name was found."
        # The Set failed, so the error skipped the run and the read.
        assert result.out is None
        panel = module("front_panels").FrontPanel.of(module("target").target)
        assert panel.get("y") is None


def test_the_front_panel_is_one_per_function(tmp_path: Path) -> None:
    package = project_with(
        tmp_path, [step("Ctrl Val.Get", GET, name="y")], "value"
    ).generate()
    with package.use() as module:
        fronts = module("front_panels")
        target = module("target").target
        assert fronts.FrontPanel.of(target) is fronts.FrontPanel.of(target)
        assert target.panel_controls == {"x": "x"}
        assert target.panel_indicators == {"y": "y"}


def test_a_set_in_a_subvi_is_the_value_the_run_in_another_uses(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", callee)

    def set_x(vi: str) -> tuple[VIContext, list[GraphNode]]:
        d = Diagram(vi)
        d.step("set", "Ctrl Val.Set", SET, name="x", value=("4", INT))
        d.wire("ref", "set.ref")
        d.wire("err_in", "set.err_in")
        d.wire("set.err_out", "err_out")
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "ref", VI_REF, "vi reference", 0),
                    pane(
                        vi,
                        "input",
                        "err_in",
                        ERROR,
                        "error in",
                        1,
                        ParsedWiringRule.RECOMMENDED,
                    ),
                ],
                outputs=[pane(vi, "output", "err_out", ERROR, "error out", 2)],
                data_flow=d.wires,
            ),
            d.nodes,
        )

    set_key = project.add_vi("SetX", set_x)

    def runner(vi: str) -> tuple[VIContext, list[GraphNode]]:
        d = Diagram(vi)
        d.nodes.append(static_reference(vi, target_key))
        d.nodes.append(
            call(
                vi,
                "setx",
                set_key,
                "iUse",
                [("ref", 0, VI_REF), ("err_in", 1, ERROR)],
                [("err_out", 2, ERROR)],
            )
        )
        d.wire("ref", "setx.ref")
        d.step("run", "Run VI", RUN)
        d.wire("ref", "run.ref")
        d.wire("setx.err_out", "run.err_in")
        d.step("get", "Ctrl Val.Get", GET, name="y")
        d.wire("ref", "get.ref")
        d.wire("run.err_out", "get.err_in")
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "out", VARIANT, "out", 0)],
                data_flow=[*d.wires, wire(vi, "get.result", "out")],
            ),
            d.nodes,
        )

    runner_key = project.add_vi("Runner", runner)
    for key in (target_key, set_key):
        project.graph._dep_graph.add_edge(runner_key, key)  # noqa: SLF001
    project.graph._dep_graph.add_edge(set_key, target_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    assert "target()" not in package.sources["runner"]
    with package.use() as module:
        assert module("runner").runner().out == 5


def test_a_reference_the_code_receives_is_a_front_panel_too(tmp_path: Path) -> None:
    """A VI reference that is a parameter is only known when the code runs."""
    project = Project(tmp_path)
    project.add_vi("Target", callee)

    def runner(vi: str) -> tuple[VIContext, list[GraphNode]]:
        d = Diagram(vi)
        d.step("set", "Ctrl Val.Set", SET, name="x", value=("9", INT))
        d.step("run", "Run VI", RUN)
        d.step("get", "Ctrl Val.Get", GET, name="y")
        for tag, nxt in (("set", "run"), ("run", "get")):
            d.wire(f"{tag}.ref_out", f"{nxt}.ref")
            d.wire(f"{tag}.err_out", f"{nxt}.err_in")
        d.wire("ref", "set.ref")
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "ref", VI_REF, "ref", 0)],
                outputs=[pane(vi, "output", "out", VARIANT, "out", 0)],
                data_flow=[*d.wires, wire(vi, "get.result", "out")],
            ),
            d.nodes,
        )

    project.add_vi("Runner", runner)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        assert module("runner").runner(ref=module("target").target).out == 10
        # A reference that is Not a Refnum is error 1026 on the node's error out.
        assert module("runner").runner(ref=None).out is None


def test_run_without_waiting_returns_while_the_vi_runs(tmp_path: Path) -> None:
    steps = [
        step("Ctrl Val.Set", SET, name="x", value=("4", INT)),
        step("Run VI", RUN, wait="False"),
    ]
    package = project_with(tmp_path, steps, None).generate()
    assert "run(False)" in package.sources["runner"]
    with package.use() as module:
        fronts = module("front_panels")
        release, ran = threading.Event(), threading.Event()

        def blocking(x: int = 0) -> object:
            release.wait(5)
            ran.set()
            return type("Result", (), {"y": x + 1})()

        blocking.panel_controls = {"x": "x"}
        blocking.panel_indicators = {"y": "y"}
        panel = fronts.FrontPanel.of(blocking)
        assert panel.state == 1
        panel.set("x", 41)
        panel.run(False)
        assert panel.state == 2
        assert not ran.is_set()
        release.set()
        assert ran.wait(5)
        while panel.state != 1:
            pass
        assert panel.get("y") == 42


def test_execution_state_reads_the_front_panel(tmp_path: Path) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", callee)

    def reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = PrimitiveNode(
            id=f"{vi}:prop",
            vi_path=vi,
            name="Property Node",
            node_type="propNode",
            object_name="VI",
            terminals=[
                Terminal(
                    id=f"{vi}:prop.ref", index=0, direction="input", lv_type=VI_REF
                ),
                Terminal(
                    id=f"{vi}:prop.value", index=4, direction="output", lv_type=INT
                ),
            ],
            properties=[
                PropertyDef(segments=[PropertySegment(name="Exec.State", code=557)])
            ],
            property_value_terminal_ids=[f"{vi}:prop.value"],
        )
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "state", INT, "state", 0)],
                data_flow=[
                    wire(vi, "ref", "prop.ref"),
                    wire(vi, "prop.value", "state"),
                ],
            ),
            [static_reference(vi, target_key), node],
        )

    runner_key = project.add_vi("Reader", reader)
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    assert "FrontPanel.of(" in package.sources["reader"]
    with package.use() as module:
        assert module("reader").reader().state == 1


def test_a_class_member_is_run_with_its_object_as_self(tmp_path: Path) -> None:
    project = Project(tmp_path)
    thing = class_type("Thing")
    project.add_class("Thing", [])

    def method(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "in", thing, "Thing in", 0)],
                outputs=[pane(vi, "output", "out", thing, "Thing out", 1)],
                data_flow=[wire(vi, "in", "out")],
            ),
            [],
        )

    member_key = project.add_method("Thing", "Echo", method, "public")

    def runner(vi: str) -> tuple[VIContext, list[GraphNode]]:
        d = Diagram(vi)
        d.nodes.append(static_reference(vi, member_key))
        d.step("set", "Ctrl Val.Set", SET, name="Thing in")
        d.step("run", "Run VI", RUN)
        d.step("get", "Ctrl Val.Get", GET, name="Thing out")
        d.wire("obj", "set.value")
        for tag in ("set", "run", "get"):
            d.wire("ref", f"{tag}.ref")
        d.wire("set.err_out", "run.err_in")
        d.wire("run.err_out", "get.err_in")
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "obj", thing, "obj", 0)],
                outputs=[pane(vi, "output", "out", VARIANT, "out", 0)],
                data_flow=[*d.wires, wire(vi, "get.result", "out")],
            ),
            d.nodes,
        )

    project.add_vi("Runner", runner)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        panel_module: ModuleType = module("front_panels")
        thing_class = module("thing").Thing
        function = thing_class.echo
        assert function.panel_controls == {"Thing in": "self"}
        assert function.panel_passed == {"Thing out": "self"}
        assert not hasattr(function, "panel_indicators")
        thing_object = thing_class()
        result = module("runner").runner(obj=thing_object)
        assert result.out is thing_object
        assert panel_module.FrontPanel.of(function).get("Thing out") is thing_object


def test_the_support_module_imports_nothing_from_lvkit() -> None:
    assert "lvkit" not in FRONT_PANELS_MODULE_SOURCE


def test_run_on_a_known_vi_with_nothing_set_stays_the_direct_call(
    tmp_path: Path,
) -> None:
    package = run_vi_project(tmp_path, None).generate()
    assert package.sources["runner"] == (
        "from __future__ import annotations\n"
        "from .target import target\n"
        "\n"
        "def runner():\n"
        "    target()"
    )


def test_a_deprecated_ctrl_val_method_is_a_gap_naming_why(tmp_path: Path) -> None:
    """The deprecated forms move the value as flattened bytes and a type string."""
    steps = [step("Ctrl Val.Set (Deprecated)", 1009, name="x")]
    gaps: list[Gap] = []
    project_with(tmp_path, steps, None).generate(gaps)
    assert [g.reason for g in gaps] == [
        "Invoke Node method 'Ctrl Val.Set (Deprecated)' on a VI reference moves the "
        "value as flattened bytes and a type string, which the generated code has "
        "no LabVIEW type to read or write"
    ]
