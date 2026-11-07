"""Run VI on a VI reference (``codegen/nodes/run_vi.py``): a call of the VI's
function with no arguments, waited for or started on the thread pool; a gap where
such a call is not the run. Each case is a handmade diagram whose generated
package is imported and run."""

from __future__ import annotations

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


def runner(
    target_key: str, wait: str | None, set_value: bool = False
) -> Recipe:
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


@pytest.mark.parametrize("wait", [None, "True"])
def test_run_vi_waiting_calls_the_function(tmp_path: Path, wait: str | None) -> None:
    package = project_with(tmp_path, wait).generate()
    assert package.gaps == []
    assert "\n    target()" in package.sources["runner"]
    with package.use() as module:
        runner_module = module("runner")
        calls: list[str] = []
        runner_module.target = lambda: calls.append("run")
        runner_module.runner()
        assert calls == ["run"]


def test_run_vi_not_waiting_starts_the_function_and_goes_on(tmp_path: Path) -> None:
    package = project_with(tmp_path, "False").generate()
    assert package.gaps == []
    assert "_async_call_pool.submit(target)" in package.sources["runner"]
    with package.use() as module:
        runner_module = module("runner")
        release, ran = threading.Event(), threading.Event()

        def blocking() -> None:
            release.wait(5)
            ran.set()

        runner_module.target = blocking
        runner_module.runner()  # returns while the VI still runs
        assert not ran.is_set()
        release.set()
        assert ran.wait(5)


@pytest.mark.parametrize(
    ("wait", "required", "set_value", "reason"),
    [
        (
            "input",
            False,
            False,
            "Run VI: Wait Until Done is computed when the diagram runs, so whether "
            "to wait for the VI is not known when the code is written.",
        ),
        (
            None,
            True,
            False,
            "Run VI passes no inputs; 'x' of Target.vi is Required.",
        ),
        (
            None,
            False,
            True,
            "Run VI: the diagram sets a value on the VI reference (Invoke Node Set "
            "Control Value), which the run would use; a call with no arguments "
            "cannot pass it.",
        ),
    ],
    ids=["computed wait", "required input", "set control value"],
)
def test_run_vi_that_a_call_cannot_be_is_a_gap(
    tmp_path: Path, wait: str | None, required: bool, set_value: bool, reason: str
) -> None:
    gaps: list[Gap] = []
    package = project_with(tmp_path, wait, required, set_value).generate(gaps)
    assert [g.reason for g in gaps if g.node_name == "Invoke Node"] == [reason]
    with package.use() as module:
        with pytest.raises(NotImplementedError, match="Run VI"):
            module("runner").runner(**({"go": True} if wait == "input" else {}))


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
    assert "    if error_in is None or not error_in.status:\n        target()" in (
        source
    )
    assert "return RunnerResult(error_out=error_in)" in source
    with package.use() as module:
        runner_module = module("runner")
        calls: list[str] = []
        runner_module.target = lambda: calls.append("run")
        assert runner_module.runner().error_out is None
        assert calls == ["run"]
        error = SimpleNamespace(status=True, code=1, source="x")
        assert runner_module.runner(error_in=error).error_out is error
        warning = SimpleNamespace(status=False, code=1, source="x")
        assert runner_module.runner(error_in=warning).error_out is warning
        assert calls == ["run", "run"]
