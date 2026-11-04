"""A Bundle/Unbundle By Name or In Place Element on a class's private data in a VI
the class does not list. LabVIEW lets only the class's own VIs reach its private
data, so such a VI is broken in LabVIEW (a VI left in the class folder after it
was removed from the class): generation warns, the node raises where it runs, and
the run is Partial. The class's own VI that does the same is unchanged."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.class_model import type_attributes
from lvkit.codegen.gaps import Gap, gap_problem
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import GraphNode, VIContext, VIMetadata
from lvkit.models import ClusterField
from lvkit.pipeline import _summary_line
from lvkit.run_report import (
    EXIT_PARTIAL,
    Outcome,
    Problem,
    ProblemKind,
    ProblemStage,
    RunReport,
)
from tests.class_graphs import INT, Project, class_type, mux, pane, wire

BASE = class_type("Base")
COUNT = ClusterField(name="Count", type=INT)
NOT_LISTED = (
    "Bundle/Unbundle By Name on the private data of class Base, but Base.lvclass "
    "does not list this VI, so the VI is broken in LabVIEW"
)
NAMES_OWNER = (
    "Bundle/Unbundle By Name on the private data of class Base, but the VI names "
    "Base.lvclass as its owner while Base.lvclass does not list it, so the VI is "
    "broken in LabVIEW"
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


def _project(tmp_path: Path, recorded_owner: bool) -> tuple[Project, str]:
    project = Project(tmp_path)
    project.add_class("Base", [COUNT])
    project.add_method("Base", "Get Count", read_count, static=False)
    orphan = project.add_vi("Orphan", read_count)
    if recorded_owner:
        project.graph._vi_metadata[orphan] = VIMetadata(  # noqa: SLF001
            owning_libraries=["Base.lvclass"]
        )
    return project, orphan


@pytest.mark.parametrize(
    ("recorded_owner", "reason"), [(False, NOT_LISTED), (True, NAMES_OWNER)]
)
def test_a_vi_the_class_does_not_list_reading_its_private_data_is_a_gap(
    tmp_path: Path, recorded_owner: bool, reason: str
) -> None:
    project, orphan = _project(tmp_path, recorded_owner)
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
        with pytest.raises(NotImplementedError, match="broken in LabVIEW"):
            module("orphan").orphan(base_in=base)
    # The gap is a problem of the run: Partial, exit 3.
    report = RunReport.of([gap_problem(gap, project.graph)])
    assert [p.kind for p in report.problems] == [ProblemKind.GAP]
    assert report.outcome() is Outcome.PARTIAL
    assert report.exit_code() == EXIT_PARTIAL


def test_the_classs_own_vi_reading_its_private_data_is_unchanged(
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
        assert module("base").Base.get_count(base) == 5


def test_an_in_place_element_outside_the_class_is_named_in_the_reason(
    tmp_path: Path,
) -> None:
    project, orphan = _project(tmp_path, recorded_owner=False)
    project.generate([])  # links each VI to the classes, as the loader does
    with pytest.raises(ConversionGap) as raised:
        type_attributes(project.graph, BASE, orphan, "In Place Element")
    assert str(raised.value) == NOT_LISTED.replace(
        "Bundle/Unbundle By Name", "In Place Element"
    )


@pytest.mark.parametrize(
    ("stopped", "failures", "gaps", "line"),
    [
        (False, {}, 0, "Complete: 4 VIs generated, no gaps"),
        (
            False,
            {ProblemKind.LVKIT_ERROR: 2},
            1,
            "Partial: 4 VIs generated, 2 VI(s) failed with an lvkit error, "
            "1 with gaps (1 gaps) — see warnings",
        ),
        (
            True,
            {ProblemKind.UNWRITABLE: 1, ProblemKind.LVKIT_ERROR: 1},
            0,
            "Stopped: 4 VIs generated, 1 VI(s) could not be written, "
            "1 VI(s) failed with an lvkit error",
        ),
    ],
)
def test_the_closing_line_says_what_happened_to_each_vi(
    stopped: bool, failures: dict[ProblemKind, int], gaps: int, line: str
) -> None:
    gap_records = [
        Gap(
            vi="V.vi",
            node_id=None,
            node_name=None,
            kind="unsupported_node",
            identifier="x",
            name="x",
            reason="why",
            detail="why",
        )
        for _ in range(gaps)
    ]
    assert _summary_line(stopped, 4, failures, gap_records) == line


def test_an_lvkit_error_in_a_run_that_went_on_is_not_called_stopped() -> None:
    """The run continued past the VI that failed: the summary says Partial with
    the lvkit error named, and the exit code is 1 (an lvkit error)."""
    report = RunReport.of(
        [Problem("V.vi", ProblemStage.GENERATE, ProblemKind.LVKIT_ERROR, "boom")]
    )
    head = report.summary_lines()[0]
    assert head == "Partial: 1 problem(s) (includes an lvkit error)"
    assert report.exit_code() == 1
