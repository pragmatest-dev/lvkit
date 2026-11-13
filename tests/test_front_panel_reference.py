"""Every VI has a front panel, reached through its reference
(``codegen/nodes/ctrl_val.py``, ``codegen/nodes/run_vi.py``): Ctrl Val.Set, Get and
Get All and Run VI through ``panel_of(reference)`` of the generated ``panels``
module. Each case is a handmade diagram whose generated package is imported and
run."""

from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import ModuleType

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.codegen.panel_description import default_functions
from lvkit.codegen.panels_module import PANELS_MODULE_SOURCE
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
from tests.helpers import without_panel_attributes

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
        empty: bool = False,
    ) -> None:
        """An Invoke Node of ``method``; ``name`` wires a String constant to its
        first parameter, ``value`` a constant (``empty``: the empty Variant
        constant) to its second, ``wait`` a Boolean constant to Run VI's Wait Until
        Done."""
        self.nodes.append(invoke(self.vi, tag, method, code))
        if name is not None:
            self.nodes.append(constant(self.vi, f"{tag}_name", name, STRING))
            self.wire(f"{tag}_name", f"{tag}.param")
        if value is not None:
            self.nodes.append(constant(self.vi, f"{tag}_value", *value))
            self.wire(f"{tag}_value", f"{tag}.value")
        if empty:
            nothing = constant(self.vi, f"{tag}_value", "", VARIANT)
            nothing.empty_variant = True
            self.nodes.append(nothing)
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
    assert "panel_of(" in source
    assert "target()" not in source
    with package.use() as module:
        result = module("runner").runner()
        # Ctrl Val.Get gives the control's value as a Variant of its type, and the
        # Set of a Variant of the same type is the value the run sees.
        variants = module("variants")
        assert result.out == variants.Variant(5, variants.LV_I32)
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
        panel = module("target").target.panel
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
        variants = module("variants")
        assert module("runner").runner().out == [
            ("y", variants.Variant(5, variants.LV_I32))
        ]
        panel = module("target").target.panel
        assert panel.get_all(True) == [("x", 4)]
        assert panel.get_all_variants(True) == [
            ("x", variants.Variant(4, variants.LV_I32))
        ]
        assert panel.get_all(False) == [("y", 5)]


def test_unknown_control_name_is_error_1_and_nothing_runs(tmp_path: Path) -> None:
    steps = [
        step("Ctrl Val.Set", SET, name="nope", value=("4", INT)),
        step("Run VI", RUN),
        step("Ctrl Val.Get", GET, name="y"),
    ]
    package = project_with(tmp_path, steps, "value").generate()
    with package.use() as module:
        result = module("runner").runner()
        assert result.error_out.code == 1
        assert result.error_out.message == '"nope" not found'
        # The Set failed, so the error skipped the run and the read: ``out`` is the
        # empty Variant, the default of a Variant.
        assert result.out == module("variants").Variant()
        panel = module("target").target.panel
        assert panel.get("y") is None


def test_the_front_panel_is_one_per_function(tmp_path: Path) -> None:
    package = project_with(
        tmp_path, [step("Ctrl Val.Get", GET, name="y")], "value"
    ).generate()
    with package.use() as module:
        panels = module("panels")
        target = module("target").target
        assert isinstance(target.panel, panels.Panel)
        assert panels.panel_of(target) is target.panel
        assert target.panel.get_all(True) == [("x", 0)]
        assert target.panel.get_all(False) == [("y", None)]
        assert module("runner").runner.panel is not target.panel


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
        variants = module("variants")
        assert module("runner").runner().out == variants.Variant(5, variants.LV_I32)


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
        variants = module("variants")
        runner = module("runner").runner
        assert runner(ref=module("target").target).out == variants.Variant(
            10, variants.LV_I32
        )
        # A reference that is Not a Refnum is error 1026 on the node's error out.
        assert runner(ref=None).out == variants.Variant()
        assert runner(ref=object()).out == variants.Variant()


def test_run_without_waiting_returns_while_the_vi_runs(tmp_path: Path) -> None:
    steps = [
        step("Ctrl Val.Set", SET, name="x", value=("4", INT)),
        step("Run VI", RUN, wait="False"),
    ]
    package = project_with(tmp_path, steps, None).generate()
    assert "run(False)" in package.sources["runner"]
    with package.use() as module:
        release, ran = threading.Event(), threading.Event()

        def blocking(x: int = 0) -> object:
            release.wait(5)
            ran.set()
            return type("Result", (), {"y": x + 1})()

        panel = module("panels").Panel(
            blocking,
            controls={"x": "x"},
            indicators={"y": "y"},
            results={"y": "y"},
        )
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


def test_the_run_is_recorded_before_the_work_is_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The thread may start the work late: the state the caller reads right after
    ``run`` is already Running, and a second run is refused with the error NI
    gives for a VI already reserved for execution."""
    package = project_with(tmp_path, [step("Ctrl Val.Get", GET, name="y")], None)
    with package.generate().use() as module:
        panels = module("panels")
        panel = module("target").target.panel
        pending: list[object] = []
        monkeypatch.setattr(panels, "_start_thread", pending.append)
        panel.run(False)
        assert panel.state == 2
        assert len(pending) == 1
        with pytest.raises(panels.PanelError) as refused:
            panel.run()
        assert refused.value.code == 1000
        assert "not in a state compatible" in refused.value.message
        pending[0]()
        assert panel.state == 1
        panel.run()  # the VI is idle again


def test_a_failed_run_is_idle_again_and_tells_the_views(tmp_path: Path) -> None:
    package = project_with(tmp_path, [step("Ctrl Val.Get", GET, name="y")], None)
    with package.generate().use() as module:
        panels = module("panels")

        def failing() -> None:
            raise ValueError("boom")

        events: list[tuple[str, object]] = []

        class Recorder(panels.PanelView):
            def run_started(self) -> None:
                events.append(("started", None))

            def run_finished(self, error: object) -> None:
                events.append(("finished", error))

            def control_changed(self, label: str, value: object) -> None:
                events.append((label, value))

        panel = panels.Panel(failing)
        panel.attach(recorder := Recorder())
        with pytest.raises(ValueError, match="boom"):
            panel.run()
        assert panel.state == 1
        assert [kind for kind, _ in events] == ["started", "finished"]
        assert isinstance(events[1][1], ValueError)
        panel.detach(recorder)
        panel.run_count = 0
        with pytest.raises(ValueError, match="boom"):
            panel.run()
        assert len(events) == 2


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
    assert "panel_of(" in package.sources["reader"]
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
        panel_module: ModuleType = module("panels")
        thing_class = module("thing").Thing
        function = thing_class.echo
        assert panel_module.panel_of(function) is function.panel
        # The class control holds its class's default object until one is set.
        [(label, default)] = function.panel.get_all(True)
        assert label == "Thing in"
        assert isinstance(default, thing_class)
        thing_object = thing_class()
        # A method's panel is reached through the object's class.
        assert getattr(type(thing_object), "echo").panel is function.panel
        result = module("runner").runner(obj=thing_object)
        assert result.out.value is thing_object
        assert function.panel.get("Thing out") is thing_object


def test_the_support_module_imports_nothing_from_lvkit() -> None:
    assert "lvkit" not in PANELS_MODULE_SOURCE


def test_a_subvi_call_stays_a_direct_call_and_leaves_the_panel_alone(
    tmp_path: Path,
) -> None:
    """The panel of a called VI is not touched by calling it as a subVI."""
    project = Project(tmp_path)
    target_key = project.add_vi("Target", callee)

    def caller(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "out", INT, "out", 0)],
                data_flow=[wire(vi, "t.y", "out")],
            ),
            [call(vi, "t", target_key, "iUse", [("x", 0, INT)], [("y", 1, INT)])],
        )

    runner_key = project.add_vi("Runner", caller)
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    package = project.generate()
    assert without_panel_attributes(package.sources["runner"]) == (
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from .target import target\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class RunnerResult:\n"
        "    out: int\n"
        "\n"
        "def runner() -> RunnerResult:\n"
        "    target_result = target()\n"
        "    return RunnerResult(out=target_result.y)"
    )
    with package.use() as module:
        assert module("runner").runner().out == 1
        assert module("target").target.panel.get("y") is None


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


def required_and_optional(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """``y`` = ``x`` + ``o``: ``x`` a Required control saved at 5, ``o`` an
    optional one saved at 3."""
    x = pane(vi, "input", "x", INT, "x", 0)
    x.default_value = 5
    o = pane(vi, "input", "o", INT, "o", 1, ParsedWiringRule.RECOMMENDED)
    o.default_value = 3
    return (
        VIContext(
            name=vi,
            inputs=[x, o],
            outputs=[pane(vi, "output", "y", INT, "y", 2)],
            data_flow=[
                wire(vi, "x", "add.x"),
                wire(vi, "o", "add.y"),
                wire(vi, "add.sum", "y"),
            ],
        ),
        [add(vi, "add")],
    )


def test_a_required_control_holds_its_saved_default_until_set(tmp_path: Path) -> None:
    """The front panel runs a VI with the values its controls hold, a Required
    control included: initially its default."""
    project = Project(tmp_path)
    project.add_vi("Target", required_and_optional)
    package = project.generate()
    assert (
        "controls={'x': 'x', 'o': 'o'}" in package.sources["target"]
        and "defaults={'x': lambda: 5}" in package.sources["target"]
    )
    with package.use() as module:
        panel = module("target").target.panel
        assert panel.get("x") == 5
        assert panel.get("o") == 3
        panel.run()
        assert panel.get("y") == 8
        panel.set("x", 10)
        assert panel.get("x") == 10
        panel.run()
        assert panel.get("y") == 13
        panel.set("o", 1)
        panel.run()
        assert panel.get("y") == 11


def test_a_required_class_control_holds_one_default_object(tmp_path: Path) -> None:
    """The panel holds one value: an unset required class control is its class's
    default object, made once and the same on every read and run."""
    cls = class_type("Cls")

    def take(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "c", cls, "c", 0)],
                outputs=[pane(vi, "output", "d", cls, "d", 1)],
                data_flow=[wire(vi, "c", "d")],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_class("Cls", [])
    project.add_vi("Take", take)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        panel = module("take").take.panel
        panel.run()
        first = panel.get("d")
        panel.run()
        second = panel.get("d")
        assert isinstance(first, module("cls").Cls)
        assert second is first


def test_a_required_array_control_holds_its_default_until_set(tmp_path: Path) -> None:
    """An unset required array control holds ``[]``, made once (a default never
    shared with another function's panel); a set value replaces it."""
    array = LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=INT)

    def take(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "a", array, "a", 0)],
                outputs=[],
                data_flow=[],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_vi("Take", take)
    package = project.generate()
    assert "controls={'a': 'a'}" in package.sources["take"]
    assert "defaults={'a': lambda: []}" in package.sources["take"]
    with package.use() as module:
        panel = module("take").take.panel
        first = panel.get("a")
        assert first == []
        assert panel.get("a") is first
        panel.run()
        assert panel.get("a") is first
        panel.set("a", [1, 2])
        assert panel.get("a") == [1, 2]
        other = module("panels").Panel(
            module("take").take, controls={"a": "a"}, defaults={"a": list}
        )
        assert other.get("a") is not first


def test_a_function_without_a_default_still_runs_with_none(tmp_path: Path) -> None:
    """A control with no default made for it, whose parameter has none either,
    holds None."""
    project = Project(tmp_path)
    project.add_vi("Target", callee)
    package = project.generate()
    seen: list[object] = []

    def other(x: int) -> None:
        seen.append(x)

    with package.use() as module:
        panel = module("panels").Panel(other, controls={"x": "x"})
        assert panel.get("x") is None
        panel.run()
        assert seen == [None]


def test_a_control_off_the_connector_pane_is_stored_but_a_run_cannot_pass_it(
    tmp_path: Path,
) -> None:
    def take(vi: str) -> tuple[VIContext, list[GraphNode]]:
        hidden = pane(vi, "input", "hidden", INT, "hidden", 0)
        hidden.is_public = False
        shown = pane(vi, "output", "shown", INT, "shown", 1)
        shown.is_public = False
        return (
            VIContext(name=vi, off_pane_inputs=[hidden], off_pane_outputs=[shown]),
            [],
        )

    project = Project(tmp_path)
    project.add_vi("Take", take)
    package = project.generate()
    assert (
        "controls={'hidden': 'hidden'}, indicators={'shown': 'shown'}, "
        "types={'hidden': LV_I32, 'shown': LV_I32}, defaults={'hidden': lambda: 0}"
        in package.sources["take"]
    )
    with package.use() as module:
        panel = module("take").take.panel
        assert panel.get("hidden") == 0
        panel.set("hidden", 3)
        assert panel.get("hidden") == 3
        panel.run()
        assert panel.get("hidden") == 3
        assert panel.get_all(False) == [("shown", None)]


def test_a_label_two_objects_share_is_the_first_in_tabbing_order(
    tmp_path: Path,
) -> None:
    """A Ctrl Val method picks "the first object whose label matches" in the front
    panel tabbing order; each object keeps its own value by its name."""

    def twice(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "a", INT, "same", 0, tab_index=1)],
                outputs=[pane(vi, "output", "b", INT, "same", 1, tab_index=0)],
                data_flow=[wire(vi, "a", "b")],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_vi("Twice", twice)
    package = project.generate()
    assert "controls={'same': 'same'}" in package.sources["twice"]
    assert "indicators={'same_2': 'same'}" in package.sources["twice"]
    assert "order=['same_2', 'same']" in package.sources["twice"]
    with package.use() as module:
        panel = module("twice").twice.panel
        # The indicator is first in tabbing order, so it answers the label.
        panel.set("same", 1)
        assert panel.value("same_2") == 1
        assert panel.value("same") == 0
        assert panel.get("same") == 1
        panel.put("same", 5)
        assert panel.get("same") == 1
        assert panel.get_all(True) == [("same", 5)]
        assert panel.get_all(False) == [("same", 1)]


def test_without_a_recorded_order_the_controls_come_first(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Twice", twice_target)
    package = project.generate()
    assert "order=" not in package.sources["twice"]
    with package.use() as module:
        panel = module("twice").twice.panel
        panel.put("same_2", 3)
        panel.set("same", 1)
        assert panel.value("same") == 1
        assert panel.value("same_2") == 3


def test_ctrl_val_on_a_shared_label_reaches_the_first_in_tabbing_order(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", twice_target)
    runner_key = project.add_vi(
        "Runner", chained(target_key, [step("Ctrl Val.Get", GET, name="same")], "value")
    )
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        panel = module("target").target.panel
        panel.put("same", 4)
        panel.put("same_2", 9)
        variants = module("variants")
        assert module("runner").runner().out == variants.Variant(4, variants.LV_I32)


def twice_target(vi: str) -> tuple[VIContext, list[GraphNode]]:
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "a", INT, "same", 0)],
            outputs=[pane(vi, "output", "b", INT, "same", 1)],
            data_flow=[wire(vi, "a", "b")],
        ),
        [],
    )


def test_panel_of_an_object_with_no_panel_is_error_1026(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Target", callee)
    with project.generate().use() as module:
        panels = module("panels")
        for reference in (None, object(), lambda: None):
            with pytest.raises(panels.PanelError) as raised:
                panels.panel_of(reference)
            assert raised.value.code == 1026


def test_every_vi_has_a_panel_with_or_without_the_panel_output(
    tmp_path: Path,
) -> None:
    """D4: the function's module is the same with ``--panel`` and without."""
    from tests.error_diagrams import Project as DiagramProject
    from tests.panel_diagrams import NAME, counting_vi

    sources: dict[bool, str] = {}
    for with_panel in (False, True):
        folder = tmp_path / str(with_panel)
        folder.mkdir()
        diagrams = DiagramProject(folder)
        diagrams.add(counting_vi())
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("lvkit.graph.loading.extract_vi_xml", diagrams.extract)
            package = diagrams.generate(NAME, panel=with_panel)
        sources[with_panel] = (package.root / "panel_demo.py").read_text()
        diagrams.close()
    assert sources[False] == sources[True]
    assert "panel_demo.panel = Panel(panel_demo," in sources[False]


class ImportingContext:
    """What a default needs of its module: a class imported from another
    generated module."""

    def __init__(self) -> None:
        self.imports: set[str] = {"from .kept import kept"}

    def class_expression(self, lv_type: LVType, what: str) -> str:
        self.imports.add("from .other import Other")
        return "Other"


def test_a_default_that_imports_a_module_does_it_inside_when_the_module_is_lazy() -> (
    None
):
    """A module in an import cycle imports other generated modules inside its
    functions, so a default naming a class of one imports it where it is made."""
    control = pane("v", "input", "c", class_type("Other"), "c", 0)
    lazy_context = ImportingContext()
    lazy = default_functions(
        [(control, "c")],
        {control.id: "c"},
        lazy_context,
        "f",
        True,  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    module = ast.fix_missing_locations(ast.Module(lazy.statements, []))
    assert ast.unparse(module) == (
        "def _f_default_0():\n    from .other import Other\n    return Other()"
    )
    assert ast.unparse(lazy.entries["c"]) == "_f_default_0"
    assert lazy_context.imports == {"from .kept import kept"}
    eager_context = ImportingContext()
    eager = default_functions(
        [(control, "c")],
        {control.id: "c"},
        eager_context,
        "f",
        False,  # type: ignore[arg-type]
    )  # type: ignore[arg-type]
    assert eager.statements == []
    assert ast.unparse(eager.entries["c"]) == "lambda: Other()"
    assert eager_context.imports == {
        "from .kept import kept",
        "from .other import Other",
    }


def test_a_thread_that_cannot_start_the_work_leaves_the_vi_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = Project(tmp_path)
    project.add_vi("Target", callee)
    with project.generate().use() as module:
        panel = module("target").target.panel

        def refusing(work: object) -> None:
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(module("panels"), "_start_thread", refusing)
        with pytest.raises(RuntimeError, match="start new thread"):
            panel.run(False)
        assert panel.state == 1


def test_an_unset_optional_array_control_holds_an_empty_array(tmp_path: Path) -> None:
    """An optional array control's parameter default is None (the function makes
    the array); the panel holds ``[]``, made once, and a run passes it."""
    array = LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=INT)

    def take(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "a", array, "a", 0, ParsedWiringRule.RECOMMENDED)
                ],
                outputs=[pane(vi, "output", "d", array, "d", 1)],
                data_flow=[wire(vi, "a", "d")],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_vi("Take", take)
    package = project.generate()
    assert "defaults={'a': lambda: []}" in package.sources["take"]
    with package.use() as module:
        panel = module("take").take.panel
        first = panel.get("a")
        assert first == []
        assert panel.get("a") is first
        panel.run()
        assert panel.get("d") == module("take").take().d == []


def test_an_unset_optional_class_control_holds_a_default_object(
    tmp_path: Path,
) -> None:
    cls = class_type("Cls")

    def take(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "c", cls, "c", 0, ParsedWiringRule.RECOMMENDED)
                ],
                outputs=[pane(vi, "output", "d", cls, "d", 1)],
                data_flow=[wire(vi, "c", "d")],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_class("Cls", [])
    project.add_vi("Take", take)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        panel = module("take").take.panel
        first = panel.get("c")
        assert isinstance(first, module("cls").Cls)
        assert panel.get("c") is first
        panel.run()
        assert isinstance(panel.get("d"), module("cls").Cls)
