"""Run VI on a VI reference (``codegen/nodes/run_vi.py``): a run of the front panel
of the VI the reference holds, waited for or started on the thread pool. Each case
is a handmade diagram whose generated package is imported and run."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import ConstantNode, GraphNode, PrimitiveNode, VIContext
from lvkit.models import ClusterField, LVType, LVTypeKind, Terminal
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import INT, Project, Recipe, pane, wire

BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
VI_REF = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="VIRefnum")
STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")


def target(required: bool) -> Recipe:
    """A VI whose output ``out`` is its input ``x`` (Required when
    ``required``, else Recommended)."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        rule = ParsedWiringRule.REQUIRED if required else ParsedWiringRule.RECOMMENDED
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "x", INT, "x", 0, rule)],
                outputs=[pane(vi, "output", "out", INT, "out", 1)],
                data_flow=[wire(vi, "x", "out")],
            ),
            [],
        )

    return build


def invoke(vi: str, tag: str, method: str, code: int) -> PrimitiveNode:
    """An Invoke Node on the VI class: reference in 0, out 1, a Boolean
    parameter at 6."""
    return PrimitiveNode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name="Invoke Node",
        node_type="invokeNode",
        object_name="VI",
        method_name=method,
        method_code=code,
        terminals=[
            Terminal(id=f"{vi}:{tag}.ref", index=0, direction="input", lv_type=VI_REF),
            Terminal(
                id=f"{vi}:{tag}.ref_out", index=1, direction="output", lv_type=VI_REF
            ),
            Terminal(id=f"{vi}:{tag}.wait", index=6, direction="input", lv_type=BOOL),
        ],
    )


def runner(target_key: str, wait: str | None, set_value: bool = False) -> Recipe:
    """A VI with a Static VI Reference to ``target_key`` wired into Run VI.
    ``wait`` is the Wait Until Done constant ("True"/"False"), None for unwired,
    or "input" for the VI's own Boolean input ``go``; ``set_value`` adds a Set
    Control Value on the same reference."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        reference = ConstantNode(
            id=f"{vi}:ref",
            vi_path=vi,
            name="Target.vi",
            value="Target.vi",
            lv_type=VI_REF,
            referenced_vi_key=target_key,
            terminals=[
                Terminal(id=f"{vi}:ref", index=0, direction="output", lv_type=VI_REF)
            ],
        )
        nodes: list[GraphNode] = [reference, invoke(vi, "run", "Run VI", 1003)]
        wires = [wire(vi, "ref", "run.ref")]
        inputs = []
        if wait == "input":
            inputs.append(pane(vi, "input", "go", BOOL, "go", 0))
            wires.append(wire(vi, "go", "run.wait"))
        elif wait is not None:
            nodes.append(
                ConstantNode(
                    id=f"{vi}:wait",
                    vi_path=vi,
                    name="Wait Until Done",
                    value=wait,
                    lv_type=BOOL,
                    terminals=[
                        Terminal(
                            id=f"{vi}:wait", index=0, direction="output", lv_type=BOOL
                        )
                    ],
                )
            )
            wires.append(wire(vi, "wait", "run.wait"))
        if set_value:
            nodes.append(invoke(vi, "set", "Set Control Value", 1004))
            wires.append(wire(vi, "ref", "set.ref"))
        return VIContext(name=vi, inputs=inputs, data_flow=wires), nodes

    return build


def project_with(
    tmp_path: Path, wait: str | None, required: bool = False, set_value: bool = False
) -> Project:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(required))
    runner_key = project.add_vi("Runner", runner(target_key, wait, set_value))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    return project


def counting_view(module):
    """A view of a panel that counts the runs it was told of."""

    class Runs(module("panels").PanelView):
        def __init__(self) -> None:
            self.started = 0
            self.finished: list[object] = []

        def run_started(self) -> None:
            self.started += 1

        def run_finished(self, error: object) -> None:
            self.finished.append(error)

    return Runs()


@pytest.mark.parametrize("wait", [None, "True"])
def test_run_vi_waiting_runs_the_front_panel(tmp_path: Path, wait: str | None) -> None:
    package = project_with(tmp_path, wait).generate()
    assert package.gaps == []
    assert "panel_of(target).run(True)" in package.sources["runner"]
    assert "_async_call_pool" not in package.sources["runner"]
    with package.use() as module:
        panel = module("target").target.panel
        panel.set("x", 7)
        module("runner").runner()
        assert panel.get("out") == 7
        assert panel.state == 1


def test_run_vi_not_waiting_goes_on_while_the_vi_runs(tmp_path: Path) -> None:
    package = project_with(tmp_path, "False").generate()
    assert package.gaps == []
    assert "run(False, _async_call_pool.submit)" in package.sources["runner"]
    with package.use() as module:
        runner_module = module("runner")
        release, ran = threading.Event(), threading.Event()

        def blocking(x: int = 0) -> object:
            release.wait(5)
            ran.set()
            return SimpleNamespace(out=x)

        panel = module("panels").Panel(
            blocking, controls={"x": "x"}, indicators={"out": "out"}
        )
        blocking.panel = panel  # type: ignore[attr-defined]
        runner_module.target = blocking
        runner_module.runner()  # returns while the VI still runs
        assert panel.state == 2
        assert not ran.is_set()
        release.set()
        assert ran.wait(5)
        while panel.state != 1:
            pass
        assert panel.get("out") == 0


def test_run_vi_with_a_computed_wait_passes_it(tmp_path: Path) -> None:
    """Wait Until Done computed when the diagram runs is the value the panel's run
    gets: TRUE waits, FALSE returns while the VI runs."""
    package = project_with(tmp_path, "input").generate()
    assert package.gaps == []
    assert "run(go, _async_call_pool.submit)" in package.sources["runner"]
    with package.use() as module:
        runner_module = module("runner")
        panel = module("target").target.panel
        runs = counting_view(module)
        panel.attach(runs)
        runner_module.runner(go=True)
        assert runs.started == 1
        runner_module.runner(go=False)
        while len(runs.finished) < 2:
            pass
        assert runs.started == 2


def test_run_vi_on_a_required_input_runs_with_its_default(tmp_path: Path) -> None:
    """A front panel runs a VI with the values its controls hold, a Required
    control included: initially its default."""
    package = project_with(tmp_path, None, required=True).generate()
    assert package.gaps == []
    with package.use() as module:
        module("runner").runner()
        assert module("target").target.panel.get("out") == 0


def test_run_vi_on_a_running_vi_is_the_nodes_error(tmp_path: Path) -> None:
    """A VI already reserved for execution cannot be run again (NI's Run VI)."""
    package = project_with(tmp_path, "False").generate()
    with package.use() as module:
        runner_module = module("runner")
        release = threading.Event()

        def blocking(x: int = 0) -> object:
            release.wait(5)
            return SimpleNamespace(out=x)

        blocking.panel = module("panels").Panel(  # type: ignore[attr-defined]
            blocking, controls={"x": "x"}, indicators={"out": "out"}
        )
        runner_module.target = blocking
        runner_module.runner()
        with pytest.raises(Exception, match="Error 1000: The VI is not in a state"):
            blocking.panel.run()
        release.set()


ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    typedef_name="error",
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=INT),
        ClusterField(name="source", type=STRING),
    ],
)


def runner_on_error_chain(target_key: str) -> Recipe:
    """A VI whose ``error in`` goes through Run VI on a Static VI Reference to
    ``target_key`` and out to its ``error out``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        reference = ConstantNode(
            id=f"{vi}:ref",
            vi_path=vi,
            name="Target.vi",
            value="Target.vi",
            lv_type=VI_REF,
            referenced_vi_key=target_key,
            terminals=[
                Terminal(id=f"{vi}:ref", index=0, direction="output", lv_type=VI_REF)
            ],
        )
        run = invoke(vi, "run", "Run VI", 1003)
        run.terminals.extend(
            [
                Terminal(
                    id=f"{vi}:run.err_in", index=2, direction="input", lv_type=ERROR
                ),
                Terminal(
                    id=f"{vi}:run.err_out", index=3, direction="output", lv_type=ERROR
                ),
            ]
        )
        context = VIContext(
            name=vi,
            inputs=[
                pane(
                    vi,
                    "input",
                    "err_in",
                    ERROR,
                    "error in",
                    0,
                    ParsedWiringRule.RECOMMENDED,
                )
            ],
            outputs=[pane(vi, "output", "err_out", ERROR, "error out", 1)],
            data_flow=[
                wire(vi, "ref", "run.ref"),
                wire(vi, "err_in", "run.err_in"),
                wire(vi, "run.err_out", "err_out"),
            ],
        )
        return context, [reference, run]

    return build


def test_run_vi_on_an_error_chain_runs_when_no_error_came_in(tmp_path: Path) -> None:
    """Run VI is an Invoke Node on the error chain: it runs the VI only when no
    error came in on its error in, and its error out is that error in."""
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))
    runner_key = project.add_vi("Runner", runner_on_error_chain(target_key))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    source = package.sources["runner"]
    assert (
        "    if error_in is None or not error_in.status:\n        try:\n"
        "            panel_of(target).run(True)"
    ) in source
    assert "error_out = error_in if error_in is not None and error_in.status" in source
    with package.use() as module:
        runner_module = module("runner")
        runs = counting_view(module)
        module("target").target.panel.attach(runs)
        assert runner_module.runner().error_out is None
        assert runs.started == 1
        error = SimpleNamespace(status=True, code=1, source="x")
        assert runner_module.runner(error_in=error).error_out is error
        warning = SimpleNamespace(status=False, code=1, source="x")
        assert runner_module.runner(error_in=warning).error_out is warning
        assert runs.started == 2


def abort_runner(target_key: str, method: str, code: int) -> Recipe:
    """A VI with a Static VI Reference to ``target_key`` wired into an Invoke
    Node of ``method`` whose output ``out`` is the VI's own output."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        reference = ConstantNode(
            id=f"{vi}:ref",
            vi_path=vi,
            name="Target.vi",
            value="Target.vi",
            lv_type=VI_REF,
            referenced_vi_key=target_key,
            terminals=[
                Terminal(id=f"{vi}:ref", index=0, direction="output", lv_type=VI_REF)
            ],
        )
        node = invoke(vi, "call", method, code)
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "out", VI_REF, "out", 0)],
                data_flow=[
                    wire(vi, "ref", "call.ref"),
                    wire(vi, "call.ref_out", "out"),
                ],
            ),
            [reference, node],
        )

    return build


@pytest.mark.parametrize(("method", "code"), [("Abort VI", 1002), ("FP.Open", 1010)])
def test_an_invoke_node_method_with_no_python_equivalent_is_a_gap(
    tmp_path: Path, method: str, code: int
) -> None:
    """Any Invoke Node method other than Run VI warns at generation and raises
    where the node runs; its output is bound to None."""
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))
    runner_key = project.add_vi("Runner", abort_runner(target_key, method, code))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    reason = f"Invoke Node method '{method}' on a VI reference has no Python equivalent"
    assert [(g.node_name, g.kind, g.reason) for g in gaps] == [
        ("Invoke Node", "unsupported_node", reason)
    ]
    assert "raise NotImplementedError(" in package.sources["runner"]
    with package.use() as module:
        with pytest.raises(NotImplementedError, match=re.escape(reason)):
            module("runner").runner()
