"""Run VI on a VI reference (``codegen/nodes/run_vi.py``): a call of the VI's
function with no arguments, waited for or started on the thread pool; a gap where
such a call is not the run. Each case is a handmade diagram whose generated
package is imported and run."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import ConstantNode, GraphNode, PrimitiveNode, VIContext
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.parser.models import ParsedWiringRule
from tests.class_graphs import INT, Project, Recipe, pane, wire

BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
VI_REF = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="VIRefnum")


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
