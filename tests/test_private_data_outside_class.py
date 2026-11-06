"""A Bundle/Unbundle By Name or In Place Element on a class's private data in a VI
the class does not list. LabVIEW lets only the class's own VIs reach its private
data, so such a VI is broken in LabVIEW (a VI left in the class folder after it
was removed from the class): generation warns, the node raises where it runs with
its outputs bound to None, and the run is Partial. The class's own VI that does
the same reads the field."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap, gap_problem
from lvkit.graph.membership import CLAIM_ATTR
from lvkit.graph.models import (
    GraphNode,
    InPlaceNode,
    PrimitiveNode,
    VIContext,
)
from lvkit.models import ClusterField, Terminal
from lvkit.run_report import EXIT_PARTIAL, Outcome, ProblemKind, RunReport
from tests.class_graphs import (
    INT,
    Project,
    class_type,
    mux,
    pane,
    run_to_gap,
    wire,
)

BASE = class_type("Base")
COUNT = ClusterField(name="Count", type=INT)


def _reason(node: str, recorded_owner: bool) -> str:
    why = (
        "the VI names that class as its owner while the class does not list it"
        if recorded_owner
        else "the class does not list this VI"
    )
    return (
        f"{node} on the private data of the class Base.lvclass, but {why}, so the "
        "VI is broken in LabVIEW"
    )


def read_count(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """Unbundle By Name of the field Count of a Base object."""
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "in", BASE, "Base in", 0)],
            outputs=[pane(vi, "output", "value", INT, "value", 1)],
            data_flow=[wire(vi, "in", "get.agg"), wire(vi, "get.field", "value")],
        ),
        [mux(vi, "get", False, BASE, INT, 0)],
    )


def read_count_in_place(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """An In Place Element Structure whose Unbundle / Bundle Elements tile reads
    the field Count of a Base object; the object comes back out."""
    structure = InPlaceNode(
        id=f"{vi}:ipe",
        vi_path=vi,
        name="In Place Element Structure",
        node_type="decomposeRecomposeStructure",
        terminals=[
            Terminal(id=f"{vi}:ipe.in", index=0, direction="input", lv_type=BASE),
            Terminal(id=f"{vi}:ipe.out", index=1, direction="output", lv_type=BASE),
        ],
    )
    tile = PrimitiveNode(
        id=f"{vi}:tile",
        vi_path=vi,
        name="Unbundle / Bundle Elements",
        parent=structure.id,
        terminals=[
            Terminal(
                id=f"{vi}:tile.agg",
                index=0,
                direction="input",
                lv_type=BASE,
                nmux_role="agg",
            ),
            Terminal(
                id=f"{vi}:tile.field",
                index=1,
                direction="output",
                lv_type=INT,
                nmux_role="list",
                nmux_field_index=0,
            ),
        ],
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "in", BASE, "Base in", 0)],
            outputs=[pane(vi, "output", "out", BASE, "Base out", 1)],
            data_flow=[wire(vi, "in", "ipe.in"), wire(vi, "ipe.out", "out")],
        ),
        [structure, tile],
    )


def _project(
    tmp_path: Path, recorded_owner: bool, recipe=read_count
) -> tuple[Project, str]:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Get Count", read_count, static=False)
    orphan = project.add_vi("Orphan", recipe)
    if recipe is read_count_in_place:
        # The tile's pairing id, which marks it a border tile of the structure.
        project.graph._graph.nodes[f"{orphan}:tile"]["poser_uid"] = "p1"  # noqa: SLF001
    if recorded_owner:
        # The VI's own record claims the class, by its path, as the loader keeps it.
        project.graph._dep_graph.nodes[orphan][CLAIM_ATTR] = project.classes[  # noqa: SLF001
            "Base"
        ]
    return project, orphan


@pytest.mark.parametrize("recorded_owner", [False, True])
def test_a_vi_the_class_does_not_list_reading_its_private_data_is_a_gap(
    tmp_path: Path, recorded_owner: bool
) -> None:
    project, orphan = _project(tmp_path, recorded_owner)
    reason = _reason("Bundle/Unbundle By Name", recorded_owner)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    (gap,) = gaps
    assert (gap.vi, gap.node_name, gap.kind, gap.reason) == (
        orphan,
        "Unbundle By Name",
        "unsupported_node",
        reason,
    )
    message = (
        f"{reason}\ngap kind: unsupported_node\nVI: Orphan.vi\nnode: Unbundle By Name"
    )
    assert f"raise NotImplementedError({message!r})" in package.sources["orphan"]
    with package.use() as module:
        base = module("base").Base()
        ran = run_to_gap(module("orphan").orphan, base_in=base)
    assert ran.message == message
    outputs = ran.outputs("base_in")
    assert outputs and set(outputs.values()) == {None}
    # The gap is a problem of the run: Partial, exit 3.
    report = RunReport.of([gap_problem(gap, project.graph)])
    assert [p.kind for p in report.problems] == [ProblemKind.GAP]
    assert report.outcome() is Outcome.PARTIAL
    assert report.exit_code() == EXIT_PARTIAL


def test_an_in_place_element_outside_the_class_raises_with_its_outputs_none(
    tmp_path: Path,
) -> None:
    project, _ = _project(tmp_path, False, read_count_in_place)
    reason = _reason("In Place Element", False)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert [(g.node_name, g.reason) for g in gaps] == [
        ("In Place Element Structure", reason)
    ]
    with package.use() as module:
        base = module("base").Base()
        ran = run_to_gap(module("orphan").orphan, base_in=base)
    assert ran.message.startswith(f"{reason}\n")
    outputs = ran.outputs("base_in")
    assert outputs and set(outputs.values()) == {None}


def test_the_classs_own_vi_reads_its_private_data_with_no_gap(
    tmp_path: Path,
) -> None:
    project, _ = _project(tmp_path, recorded_owner=True)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert [g.vi for g in gaps] == [project.vis["Orphan"]]
    assert "NotImplementedError" not in package.sources["base"]
    with package.use() as module:
        base = module("base").Base()
        base._Base__count = 5
        assert module("base").Base.get_count(base).value == 5
