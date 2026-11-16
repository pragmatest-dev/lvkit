"""Front Panel Window:Title on the VI's own window
(``codegen/nodes/front_panel_title.py``): a write is ``live_io.set_window_title``
and the function keeps the title for its later reads; a read before any write, or
in a VI that never writes, is a gap (the VI file does not record its saved window
title); on another VI's reference it stays a gap. Each case is a handmade diagram
whose generated package is imported and run."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import ConstantNode, GraphNode, PrimitiveNode, VIContext
from lvkit.models import PropertyDef, PropertySegment, Terminal
from tests.class_graphs import Project, Recipe, pane, wire
from tests.live_io_recorder import Recorder
from tests.test_run_vi import STRING, VI_REF, target

TITLE_CODE = 553


def title_node(vi: str, rows: str) -> PrimitiveNode:
    """A Property Node on the VI class with one Title row per letter of ``rows``:
    ``w`` a write (terminal ``prop.row<i>``, an input), ``r`` a read (an output).
    The reference is unwired (the default VI object)."""
    terminals = [
        Terminal(id=f"{vi}:prop.ref", index=0, direction="input", lv_type=VI_REF)
    ]
    for i, kind in enumerate(rows):
        terminals.append(
            Terminal(
                id=f"{vi}:prop.row{i}",
                index=4 + i,
                direction="input" if kind == "w" else "output",
                lv_type=STRING,
            )
        )
    return PrimitiveNode(
        id=f"{vi}:prop",
        vi_path=vi,
        name="Property Node",
        node_type="propNode",
        object_name="VI",
        terminals=terminals,
        properties=[
            PropertyDef(
                segments=[
                    PropertySegment(name="Front Panel Window:Title", code=TITLE_CODE)
                ]
            )
            for _ in rows
        ],
        property_value_terminal_ids=[f"{vi}:prop.row{i}" for i in range(len(rows))],
    )


def own_title(rows: str) -> Recipe:
    """A VI whose input ``t`` is written to every write row of the Title and whose
    output ``out`` is the last read row."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        wires = []
        for i, kind in enumerate(rows):
            if kind == "w":
                wires.append(wire(vi, "t", f"prop.row{i}"))
            else:
                wires.append(wire(vi, f"prop.row{i}", "out"))
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "t", STRING, "t", 0)],
                outputs=[pane(vi, "output", "out", STRING, "out", 1)],
                data_flow=wires,
            ),
            [title_node(vi, rows)],
        )

    return build


def test_a_write_then_a_read_gives_the_title_written(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Own", own_title("wr"))
    package = project.generate()
    assert package.gaps == []
    source = package.sources["own"]
    assert "live_io.set_window_title(" in source
    with package.use() as module:
        own = module("own").own
        assert own(t="Hello").out == "Hello"
        recorder = Recorder()
        assert own(t="Report", live_io=recorder).out == "Report"
        assert [c for c in recorder.log if c[0] != "write"] == [
            ("window_title", "Report")
        ]


def test_a_second_call_starts_without_the_first_calls_title(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Own", own_title("rw"))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        own = module("own").own
        with pytest.raises(NotImplementedError, match=r"read before any write.*None"):
            own(t="A")


def test_a_title_only_read_is_a_gap(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Own", own_title("r"))
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert len(gaps) == 1
    assert "the VI file does not record" in gaps[0].reason
    assert "live_io" not in package.sources["own"]


def test_on_another_vis_reference_stays_a_gap(tmp_path: Path) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))

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
        node = title_node(vi, "r")
        return (
            VIContext(
                name=vi,
                outputs=[pane(vi, "output", "out", STRING, "out", 0)],
                data_flow=[
                    wire(vi, "ref", "prop.ref"),
                    wire(vi, "prop.row0", "out"),
                ],
            ),
            [reference, node],
        )

    runner_key = project.add_vi("Runner", build)
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert len(gaps) == 1
    assert "Front Panel Window:Title" in gaps[0].reason
    assert "live_io" not in package.sources["runner"]
