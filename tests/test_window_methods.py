"""FP.Open, FP.Close and Abort VI on the reference of another VI
(``codegen/nodes/front_panel_window.py``) and SubPanel Insert VI and Remove VI
(``codegen/nodes/subpanel.py``): a call on that VI's panel, which records the state
and tells the views attached to it. Each case is a handmade diagram whose generated
package is imported and run."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext
from lvkit.models import LVType, LVTypeKind, Terminal
from tests.class_graphs import INT, Project, Recipe, call, pane, wire
from tests.test_front_panel_reference import (
    BOOL,
    ERROR,
    VI_REF,
    chained,
    static_reference,
    step,
)
from tests.test_window_rows import Watcher, _target

FP_OPEN, FP_CLOSE, ABORT = 1080, 1061, 1004
INSERT_VI, REMOVE_VI = 1918385152, 1918385153


def project_with(tmp_path: Path, steps: list[tuple]) -> Project:
    """A Runner that calls ``steps`` (``step(...)``) on a static reference of Target,
    FP.Open's Activate? a Boolean and State an integer."""
    project = Project(tmp_path)
    target_key = project.add_vi("Target", lambda vi: (_target(vi), []))
    build = chained(target_key, steps, None)

    def runner(vi: str) -> tuple[VIContext, list[GraphNode]]:
        context, nodes = build(vi)
        for node in nodes:
            if isinstance(node, PrimitiveNode) and node.method_name:
                for terminal in node.terminals:
                    if terminal.index == 6:
                        terminal.lv_type = BOOL
                    elif terminal.index == 8:
                        terminal.lv_type = INT
        return context, nodes

    runner_key = project.add_vi("Runner", runner)
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    return project


def test_fp_open_and_close_on_a_reference_record_state_and_tell_the_views(
    tmp_path: Path,
) -> None:
    steps = [
        step("FP.Open", FP_OPEN, wait="True", value=("3", INT)),
        step("FP.Close", FP_CLOSE),
    ]
    gaps: list[Gap] = []
    package = project_with(tmp_path, steps[:1]).generate(gaps)
    assert gaps == []
    with package.use() as module:
        panel = module("target").target.panel
        watcher = Watcher(module("panels").PanelView)
        panel.attach(watcher.view)
        assert panel.window_state == 2
        assert module("runner").runner().error_out is None
        assert panel.window_state == 3
        assert watcher.told == [("window", 3, True)]
    (tmp_path / "close").mkdir()
    package = project_with(tmp_path / "close", steps).generate()
    with package.use() as module:
        panel = module("target").target.panel
        watcher = Watcher(module("panels").PanelView)
        panel.attach(watcher.view)
        assert module("runner").runner().error_out is None
        assert panel.window_state == 2
        assert watcher.told == [("window", 3, True), ("window", 2, False)]


@pytest.mark.parametrize("state", [0, 2])
def test_fp_open_in_state_invalid_or_closed_is_error_1_on_error_out(
    tmp_path: Path, state: int
) -> None:
    steps = [step("FP.Open", FP_OPEN, value=(str(state), INT))]
    package = project_with(tmp_path, steps).generate()
    with package.use() as module:
        assert module("runner").runner().error_out.code == 1
        assert module("target").target.panel.window_state == 2


def test_fp_open_on_a_reference_uses_nis_defaults(tmp_path: Path) -> None:
    package = project_with(tmp_path, [step("FP.Open", FP_OPEN)]).generate()
    assert "open_window(False, 1)" in package.sources["runner"]
    with package.use() as module:
        module("runner").runner()
        assert module("target").target.panel.window_state == 1


def test_abort_vi_on_an_idle_vi_does_nothing_and_ends_a_running_one(
    tmp_path: Path,
) -> None:
    package = project_with(tmp_path, [step("Abort VI", ABORT)]).generate()
    assert package.gaps == []
    assert "panel_of(" in package.sources["runner"]
    with package.use() as module:
        panels = module("panels")
        assert module("runner").runner().error_out is None
        panel = module("target").target.panel
        assert panel.aborted is False

        def spin(live_io: Any) -> None:
            while True:
                live_io.read("x", 0)

        running = panels.Panel(spin)
        spin.panel = running  # type: ignore[attr-defined]
        module("target").target.panel = running
        running.run(wait=False)
        assert module("runner").runner().error_out is None
        for _ in range(100):
            if running.state == 1:
                break
            time.sleep(0.05)
        assert running.state == 1
        assert running.aborted is True


def test_abort_vi_on_a_subvi_is_error_1000(tmp_path: Path) -> None:
    def inner(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = invoke_abort(vi)
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "err", ERROR, "error out", 0)],
                data_flow=[wire(vi, "abort.err_out", "err")],
            ),
            [node],
        )

    project = Project(tmp_path)
    inner_key = project.add_vi("Inner", inner)

    def outer(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "err", ERROR, "error out", 0)],
                data_flow=[wire(vi, "c.err", "err")],
            ),
            [call(vi, "c", inner_key, "iUse", [], [("err", 0, ERROR)])],
        )

    outer_key = project.add_vi("Outer", outer)
    project.graph._dep_graph.add_edge(outer_key, inner_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        assert module("inner").inner().error_out is None
        assert module("outer").outer().error_out.code == 1000


def invoke_abort(vi: str) -> PrimitiveNode:
    """An Abort VI Invoke Node on the default VI object (no reference wired)."""
    return PrimitiveNode(
        id=f"{vi}:abort",
        vi_path=vi,
        name="Invoke Node",
        node_type="invokeNode",
        object_name="VI",
        method_name="Abort VI",
        method_code=ABORT,
        terminals=[
            Terminal(id=f"{vi}:abort.ref", index=0, direction="input", lv_type=VI_REF),
            Terminal(
                id=f"{vi}:abort.ref_out", index=1, direction="output", lv_type=VI_REF
            ),
            Terminal(
                id=f"{vi}:abort.err_in", index=2, direction="input", lv_type=ERROR
            ),
            Terminal(
                id=f"{vi}:abort.err_out", index=3, direction="output", lv_type=ERROR
            ),
        ],
    )


def subpanel_node(vi: str, method: str, code: int, bound: str | None) -> PrimitiveNode:
    """An Invoke Node of a SubPanel control: bound to the control with uid ``bound``,
    its VI Ref parameter at 6."""
    return PrimitiveNode(
        id=f"{vi}:sub",
        vi_path=vi,
        name="Invoke Node",
        node_type="invokeNode",
        object_name="SubPanel",
        method_name=method,
        method_code=code,
        bound_control_uid=bound,
        terminals=[
            Terminal(
                id=f"{vi}:sub.ref",
                index=0,
                direction="input",
                lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum"),
            ),
            Terminal(id=f"{vi}:sub.err_in", index=2, direction="input", lv_type=ERROR),
            Terminal(
                id=f"{vi}:sub.err_out", index=3, direction="output", lv_type=ERROR
            ),
            Terminal(id=f"{vi}:sub.vi", index=6, direction="input", lv_type=VI_REF),
        ],
    )


def subpanel_runner(target_key: str, methods: list[tuple[str, int]]) -> Recipe:
    """A VI that inserts and removes the Target VI in its SubPanel control."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        nodes: list[GraphNode] = [static_reference(vi, target_key)]
        wires = []
        previous = None
        for index, (method, code) in enumerate(methods):
            node = subpanel_node(vi, method, code, "211")
            node.id = f"{vi}:sub{index}"
            for terminal in node.terminals:
                terminal.id = terminal.id.replace(":sub.", f":sub{index}.")
            nodes.append(node)
            if code == INSERT_VI:
                wires.append(wire(vi, "ref", f"sub{index}.vi"))
            if previous is not None:
                wires.append(wire(vi, f"sub{previous}.err_out", f"sub{index}.err_in"))
            previous = index
        wires.append(wire(vi, f"sub{previous}.err_out", "err"))
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "err", ERROR, "error out", 0)],
                data_flow=wires,
            ),
            nodes,
        )

    return build


def test_subpanel_insert_and_remove_vi_record_and_tell_the_views(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", lambda vi: (_target(vi), []))
    runner_key = project.add_vi(
        "Runner",
        subpanel_runner(
            target_key, [("Insert VI", INSERT_VI), ("Remove VI", REMOVE_VI)]
        ),
    )
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        runner = module("runner").runner
        target = module("target").target
        watcher = Watcher(module("panels").PanelView)
        runner.panel.attach(watcher.view)
        assert runner().error_out is None
        assert watcher.told == [("subpanel", "211", target), ("subpanel", "211", None)]
        # loaded in a SubPanel the window state reads Hidden and cannot be set
        runner.panel.insert_vi("211", target)
        assert target.panel.window_state == 3
        with pytest.raises(module("panels").PanelError) as raised:
            target.panel.set_window_state(1)
        assert raised.value.code == 1000
        runner.panel.remove_vi("211")
        assert target.panel.window_state == 2


def test_subpanel_insert_vi_of_an_open_front_panel_is_error_1000(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", lambda vi: (_target(vi), []))
    runner_key = project.add_vi(
        "Runner", subpanel_runner(target_key, [("Insert VI", INSERT_VI)])
    )
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    package = project.generate()
    with package.use() as module:
        module("target").target.panel.open_window()
        assert module("runner").runner().error_out.code == 1000
