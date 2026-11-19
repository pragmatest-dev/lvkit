"""Front Panel Window:Title (``codegen/nodes/front_panel_title.py``): every
generated function carries its VI's window title as ``function.window_title`` (the
file's custom Window title, else the VI's name); a read of the property on the VI's
own window or on a VI reference gives it and a write sets it, and a write on the
VI's own window also calls ``live_io.set_window_title``. Each case is a handmade
diagram whose generated package is imported and run."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.models import (
    ConstantNode,
    GraphNode,
    PrimitiveNode,
    VIContext,
    VIProperties,
    WindowProps,
)
from lvkit.models import PropertyDef, PropertySegment, Terminal
from tests.class_graphs import Project, Recipe, pane, wire
from tests.live_io_recorder import Recorder
from tests.test_run_vi import STRING, VI_REF, target

TITLE_CODE = 553


def title_node(
    vi: str, rows: str, name: str = "Front Panel Window:Title"
) -> PrimitiveNode:
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
            PropertyDef(segments=[PropertySegment(name=name, code=TITLE_CODE)])
            for _ in rows
        ],
        property_value_terminal_ids=[f"{vi}:prop.row{i}" for i in range(len(rows))],
    )


def own_title(rows: str, name: str = "Front Panel Window:Title") -> Recipe:
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
            [title_node(vi, rows, name)],
        )

    return build


def other_title(target_key: str, rows: str) -> Recipe:
    """A VI whose Title Property Node is on the reference of the VI ``target_key``
    (a Static VI Reference), written from its input ``t`` and read to ``out``."""

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
        wires = [wire(vi, "ref", "prop.ref")]
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
            [reference, title_node(vi, rows)],
        )

    return build


def titled(project: Project, key: str, title: str | None) -> None:
    """Record the custom Window title of the VI ``key`` (None: no ``<TITL>``)."""
    project.graph._vi_properties[key] = VIProperties(  # noqa: SLF001
        window=WindowProps(window_title=title)
    )


def test_a_read_before_any_write_gives_the_recorded_title(tmp_path: Path) -> None:
    project = Project(tmp_path)
    key = project.add_vi("Own", own_title("r"))
    titled(project, key, "Recorded Title")
    package = project.generate()
    assert package.gaps == []
    assert "live_io" not in package.sources["own"]
    with package.use() as module:
        assert module("own").own(t="").out == "Recorded Title"


def test_a_read_before_any_write_without_a_title_gives_the_vi_name(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_vi("Own", own_title("r"))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        assert module("own").own(t="").out == "Own.vi"


def test_the_older_name_of_the_property_reads_the_same(tmp_path: Path) -> None:
    project = Project(tmp_path)
    key = project.add_vi("Own", own_title("r", "Front Panel.Title"))
    titled(project, key, "Recorded Title")
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        assert module("own").own(t="").out == "Recorded Title"


def test_a_write_then_a_read_gives_the_title_written(tmp_path: Path) -> None:
    project = Project(tmp_path)
    key = project.add_vi("Own", own_title("wr"))
    titled(project, key, "Recorded Title")
    package = project.generate()
    assert package.gaps == []
    assert "live_io.set_window_title(" in package.sources["own"]
    with package.use() as module:
        own = module("own").own
        assert own(t="Hello").out == "Hello"
        recorder = Recorder()
        assert own(t="Report", live_io=recorder).out == "Report"
        assert [c for c in recorder.log if c[0] != "write"] == [
            ("window_title", "Report")
        ]


def test_a_read_before_the_write_gives_the_recorded_title(tmp_path: Path) -> None:
    project = Project(tmp_path)
    key = project.add_vi("Own", own_title("rw"))
    titled(project, key, "Recorded Title")
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        own = module("own").own
        assert own(t="A").out == "Recorded Title"
        # The title belongs to the VI: the write of the first call is its title.
        assert own(t="B").out == "A"


def test_on_another_vis_reference_a_read_gives_its_title(tmp_path: Path) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))
    titled(project, target_key, "Target Title")
    project.add_vi("Runner", other_title(target_key, "r"))
    project.graph._dep_graph.add_edge(  # noqa: SLF001
        project.vis["Runner"], target_key
    )
    package = project.generate()
    assert package.gaps == []
    assert "live_io" not in package.sources["runner"]
    with package.use() as module:
        assert module("runner").runner(t="").out == "Target Title"


def test_on_another_vis_reference_without_a_title_a_read_gives_its_name(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))
    project.add_vi("Runner", other_title(target_key, "r"))
    project.graph._dep_graph.add_edge(  # noqa: SLF001
        project.vis["Runner"], target_key
    )
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        assert module("runner").runner(t="").out == "Target.vi"


def test_on_another_vis_reference_a_write_sets_its_title(tmp_path: Path) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))
    titled(project, target_key, "Target Title")
    project.add_vi("Runner", other_title(target_key, "wr"))
    project.graph._dep_graph.add_edge(  # noqa: SLF001
        project.vis["Runner"], target_key
    )
    package = project.generate()
    assert package.gaps == []
    assert "live_io" not in package.sources["runner"]
    with package.use() as module:
        assert module("runner").runner(t="New Title").out == "New Title"
        assert module("target").target.window_title == "New Title"
