"""FP.Open and FP.Close on the VI's own front panel
(``codegen/nodes/front_panel_window.py``): the function calls ``live_io.open_panel``
and ``live_io.close_panel``, which do nothing without a ``live_io`` that shows
windows. On the reference of another VI both stay a gap. Each case is a handmade
diagram whose generated package is imported and run."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import ConstantNode, GraphNode, VIContext
from lvkit.models import Terminal
from tests.class_graphs import INT, Project, Recipe, pane, wire
from tests.live_io_recorder import Recorder
from tests.test_run_vi import BOOL, abort_runner, invoke, target

FP_OPEN = 1080
FP_CLOSE = 1061
STATE = 8
ACTIVATE = 6


def constant(vi: str, tag: str, value: str, lv_type, terminal: str) -> ConstantNode:
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


def own_window(activate: str | None = "True", state: str | None = "3") -> Recipe:
    """A VI whose diagram opens its own window, passes its input ``x`` to its
    output ``out``, then closes the window. The Invoke Nodes have no reference
    wired in (the default VI object); the open's reference out is wired into the
    close's reference in, which orders them."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        open_node = invoke(vi, "open", "FP.Open", FP_OPEN)
        open_node.terminals.append(
            Terminal(id=f"{vi}:open.state", index=STATE, direction="input", lv_type=INT)
        )
        close_node = invoke(vi, "close", "FP.Close", FP_CLOSE)
        nodes: list[GraphNode] = [open_node, close_node]
        wires = [wire(vi, "open.ref_out", "close.ref"), wire(vi, "x", "out")]
        if activate is not None:
            nodes.append(constant(vi, "activate", activate, BOOL, "open.wait"))
            wires.append(wire(vi, "activate", "open.wait"))
        if state is not None:
            nodes.append(constant(vi, "state", state, INT, "open.state"))
            wires.append(wire(vi, "state", "open.state"))
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "x", INT, "x", 0)],
                outputs=[pane(vi, "output", "out", INT, "out", 1)],
                data_flow=wires,
            ),
            nodes,
        )

    return build


def test_fp_open_and_close_on_its_own_panel_call_live_io(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Own", own_window())
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    source = package.sources["own"]
    assert "live_io.open_panel(True, 3)" in source
    assert "live_io.close_panel()" in source
    assert source.index("open_panel") < source.index("close_panel")
    with package.use() as module:
        own = module("own").own
        assert own(x=5).out == 5
        recorder = Recorder()
        assert own(x=7, live_io=recorder).out == 7
        assert [c for c in recorder.log if c[0] != "write"] == [
            ("open_panel", True, 3),
            ("close_panel",),
        ]


def test_unwired_inputs_take_nis_defaults(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Own", own_window(activate=None, state=None))
    package = project.generate()
    assert package.gaps == []
    assert "live_io.open_panel(False, 1)" in package.sources["own"]
    with package.use() as module:
        recorder = Recorder()
        module("own").own(x=1, live_io=recorder)
        assert [c for c in recorder.log if c[0] != "write"] == [
            ("open_panel", False, 1),
            ("close_panel",),
        ]


@pytest.mark.parametrize(
    ("method", "code"), [("FP.Open", FP_OPEN), ("FP.Close", FP_CLOSE)]
)
def test_on_another_vis_reference_stays_a_gap(
    tmp_path: Path, method: str, code: int
) -> None:
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
    assert "live_io" not in package.sources["runner"]


def test_a_run_through_the_panel_goes_on_past_the_window_calls(tmp_path: Path) -> None:
    """The panel's own ``live_io`` has no window to open or close, so it keeps the
    base behaviour and the run ends with the indicator stored."""
    project = Project(tmp_path)
    project.add_vi("Own", own_window())
    package = project.generate()
    with package.use() as module:
        panel = module("own").own.panel
        panel.set("x", 4)
        panel.run(wait=True)
        assert panel.get("out") == 4
