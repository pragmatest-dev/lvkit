"""The front panel window's bounds and position and the panel's deferred redraws:
Front Panel Window:Window Bounds (``codegen/nodes/front_panel_bounds.py``), FP.Center
(``codegen/nodes/front_panel_window.py``) and the Defer Panel Updates of a Pnl
reference (``codegen/nodes/panel_reference.py``). Each generated function carries
its VI's saved bounds as ``function.window_bounds``; every case is a handmade
diagram whose generated package is imported and run."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lvkit.graph.models import (
    ConstantNode,
    GraphNode,
    PrimitiveNode,
    VIContext,
    VIProperties,
    WindowProps,
)
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    PropertyDef,
    PropertySegment,
    Terminal,
)
from lvkit.panel.runtime.view import PanelWidgets
from lvkit.parser.utils import saved_window_bounds
from tests.class_graphs import Project, Recipe, pane, wire
from tests.live_io_recorder import Recorder
from tests.test_control_references import (
    REFNUM,
    ordered_target,
    property_node,
)
from tests.test_front_panel_reference import BOOL, ERROR, VI_REF, static_reference
from tests.test_run_vi import abort_runner, invoke, target

BOUNDS_CODE = 536
CENTER_CODE = 1089
DEFER_CODE = 104105989
I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
ELEMENTS = ["Left", "Top", "Right", "Bottom"]
TYPEDEF_BOUNDS = LVType(
    kind=LVTypeKind.CLUSTER,
    typedef_name="LVRectTypeDef.ctl",
    fields=[ClusterField(name=name, type=I32) for name in ELEMENTS],
)
ANONYMOUS_BOUNDS = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[ClusterField(name=name, type=I32) for name in ELEMENTS],
)
SAVED = (25, 94, 682, 591)


def bounds_node(vi: str, rows: str, lv_type: LVType) -> PrimitiveNode:
    """A Property Node on the VI class with one Window Bounds row per letter of
    ``rows``: ``w`` a write (terminal ``prop.row<i>``, an input), ``r`` a read (an
    output). The reference is wired from ``prop.ref`` when the recipe does so."""
    terminals = [
        Terminal(id=f"{vi}:prop.ref", index=0, direction="input", lv_type=VI_REF)
    ]
    for i, kind in enumerate(rows):
        terminals.append(
            Terminal(
                id=f"{vi}:prop.row{i}",
                index=4 + i,
                direction="input" if kind == "w" else "output",
                lv_type=lv_type,
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
                    PropertySegment(name="Front Panel.WinBounds", code=BOUNDS_CODE)
                ]
            )
            for _ in rows
        ],
        property_value_terminal_ids=[f"{vi}:prop.row{i}" for i in range(len(rows))],
    )


def bounds_vi(rows: str, lv_type: LVType, target_key: str | None = None) -> Recipe:
    """A VI whose input ``b`` is written to every write row and whose output
    ``out`` is the last read row; with ``target_key``, the node is on the
    reference of that VI (a Static VI Reference), else on its own window."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        wires = []
        nodes: list[GraphNode] = [bounds_node(vi, rows, lv_type)]
        if target_key is not None:
            reference = ConstantNode(
                id=f"{vi}:ref",
                vi_path=vi,
                name="Target.vi",
                value="Target.vi",
                lv_type=VI_REF,
                referenced_vi_key=target_key,
                terminals=[
                    Terminal(
                        id=f"{vi}:ref", index=0, direction="output", lv_type=VI_REF
                    )
                ],
            )
            nodes.insert(0, reference)
            wires.append(wire(vi, "ref", "prop.ref"))
        for i, kind in enumerate(rows):
            if kind == "w":
                wires.append(wire(vi, "b", f"prop.row{i}"))
            else:
                wires.append(wire(vi, f"prop.row{i}", "out"))
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "b", lv_type, "b", 0)],
                outputs=[pane(vi, "output", "out", lv_type, "out", 1)],
                data_flow=wires,
            ),
            nodes,
        )

    return build


def save_bounds(
    project: Project, key: str, bounds: tuple[int, int, int, int] | None
) -> None:
    """Record the saved window bounds of the VI ``key`` (None: the file has none)."""
    project.graph._vi_properties[key] = VIProperties(  # noqa: SLF001
        window=WindowProps(window_bounds=bounds)
    )


def as_tuple(cluster: Any) -> tuple[int, ...]:
    if isinstance(cluster, SimpleNamespace):
        return (cluster.left, cluster.top, cluster.right, cluster.bottom)
    return tuple(cluster)


def make(lv_type: LVType, values: tuple[int, int, int, int]) -> Any:
    if lv_type is ANONYMOUS_BOUNDS:
        return values
    return SimpleNamespace(
        left=values[0], top=values[1], right=values[2], bottom=values[3]
    )


@pytest.mark.parametrize("lv_type", [TYPEDEF_BOUNDS, ANONYMOUS_BOUNDS])
def test_a_read_before_any_write_gives_the_saved_bounds(
    tmp_path: Path, lv_type: LVType
) -> None:
    project = Project(tmp_path)
    key = project.add_vi("Own", bounds_vi("r", lv_type))
    save_bounds(project, key, SAVED)
    package = project.generate()
    assert package.gaps == []
    assert "live_io" not in package.sources["own"]
    assert "own.window_bounds = (25, 94, 682, 591)" in package.sources["own"]
    with package.use() as module:
        own = module("own").own
        assert own.window_bounds == SAVED
        result = own(b=make(lv_type, (0, 0, 0, 0))).out
        assert as_tuple(result) == SAVED
        assert isinstance(result, SimpleNamespace) == (lv_type is TYPEDEF_BOUNDS)


@pytest.mark.parametrize("lv_type", [TYPEDEF_BOUNDS, ANONYMOUS_BOUNDS])
def test_a_write_then_a_read_gives_the_bounds_written_and_tells_live_io(
    tmp_path: Path, lv_type: LVType
) -> None:
    project = Project(tmp_path)
    key = project.add_vi("Own", bounds_vi("wr", lv_type))
    save_bounds(project, key, SAVED)
    package = project.generate()
    assert package.gaps == []
    assert "live_io.set_window_bounds(" in package.sources["own"]
    with package.use() as module:
        own = module("own").own
        assert as_tuple(own(b=make(lv_type, (1, 2, 3, 4))).out) == (1, 2, 3, 4)
        assert own.window_bounds == (1, 2, 3, 4)
        recorder = Recorder()
        assert as_tuple(
            own(b=make(lv_type, (10, 20, 30, 40)), live_io=recorder).out
        ) == (10, 20, 30, 40)
        assert [c for c in recorder.log if c[0] != "write"] == [
            ("window_bounds", (10, 20, 30, 40))
        ]


def test_a_file_that_records_no_bounds_reads_none(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Own", bounds_vi("r", TYPEDEF_BOUNDS))
    package = project.generate()
    assert package.gaps == []
    assert "own.window_bounds = None" in package.sources["own"]
    with package.use() as module:
        assert module("own").own(b=make(TYPEDEF_BOUNDS, (0, 0, 0, 0))).out is None


def test_on_another_vis_reference_a_read_gives_its_saved_bounds(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))
    save_bounds(project, target_key, SAVED)
    runner_key = project.add_vi("Runner", bounds_vi("r", TYPEDEF_BOUNDS, target_key))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    assert "live_io" not in package.sources["runner"]
    with package.use() as module:
        result = module("runner").runner(b=make(TYPEDEF_BOUNDS, (0, 0, 0, 0))).out
        assert as_tuple(result) == SAVED


def test_on_another_vis_reference_a_write_sets_its_bounds(tmp_path: Path) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))
    save_bounds(project, target_key, SAVED)
    runner_key = project.add_vi("Runner", bounds_vi("wr", ANONYMOUS_BOUNDS, target_key))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    assert "live_io" not in package.sources["runner"]
    with package.use() as module:
        assert as_tuple(module("runner").runner(b=(7, 8, 9, 10)).out) == (7, 8, 9, 10)
        assert module("target").target.window_bounds == (7, 8, 9, 10)


def test_a_terminal_that_is_not_four_elements_is_a_gap(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Own", bounds_vi("r", I32))
    package = project.generate()
    assert [g.reason for g in package.gaps] == [
        "Front Panel Window:Window Bounds: the property's terminal is not a cluster "
        "of the 4 elements NI's Window Bounds has"
    ]


def own_center(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that centers its own window (FP.Center on the default VI object) and
    passes its input ``x`` to its output ``out``."""
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "x", I32, "x", 0)],
            outputs=[pane(vi, "output", "out", I32, "out", 1)],
            data_flow=[wire(vi, "x", "out")],
        ),
        [invoke(vi, "center", "FP.Center", CENTER_CODE)],
    )


def test_fp_center_on_its_own_panel_calls_live_io_and_keeps_the_bounds(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    key = project.add_vi("Own", own_center)
    save_bounds(project, key, SAVED)
    package = project.generate()
    assert package.gaps == []
    assert "live_io.center_panel()" in package.sources["own"]
    with package.use() as module:
        own = module("own").own
        assert own(x=5).out == 5
        recorder = Recorder()
        assert own(x=6, live_io=recorder).out == 6
        assert [c for c in recorder.log if c[0] != "write"] == [("center_panel",)]
        assert own.window_bounds == SAVED


def test_fp_center_on_another_vis_reference_does_nothing_in_this_function(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", target(False))
    save_bounds(project, target_key, SAVED)
    runner_key = project.add_vi(
        "Runner", abort_runner(target_key, "FP.Center", CENTER_CODE)
    )
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    assert "live_io" not in package.sources["runner"]
    with package.use() as module:
        assert module("runner").runner().out is module("target").target
        assert module("target").target.window_bounds == SAVED


def defer_vi(target_key: str, rows: str) -> Recipe:
    """A VI that writes its Boolean input ``flag`` to the Defer Panel Updates of the
    panel of a static reference (``w``) and reads it to ``out`` (``r``), in order."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        panel = property_node(vi, "panel", "VI", [("panel", (573,), "output", REFNUM)])
        defer = property_node(
            vi,
            "defer",
            "Pnl",
            [
                (f"row{i}", (DEFER_CODE,), "input" if kind == "w" else "output", BOOL)
                for i, kind in enumerate(rows)
            ],
        )
        wires = [
            wire(vi, "ref", "panel.ref"),
            wire(vi, "panel.panel", "defer.ref"),
            wire(vi, "panel.err_out", "defer.err_in"),
            wire(vi, "defer.err_out", "err"),
        ]
        for i, kind in enumerate(rows):
            wires.append(
                wire(vi, "flag", f"defer.row{i}")
                if kind == "w"
                else wire(vi, f"defer.row{i}", "out")
            )
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "flag", BOOL, "flag", 0)],
                outputs=[
                    pane(vi, "output", "out", BOOL, "out", 1),
                    pane(vi, "output", "err", ERROR, "error out", 2),
                ],
                data_flow=wires,
            ),
            [static_reference(vi, target_key), panel, defer],
        )

    return build


def test_defer_panel_updates_reads_false_then_the_last_write(tmp_path: Path) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", ordered_target)
    runner_key = project.add_vi("Runner", defer_vi(target_key, "rw"))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    assert "live_io" not in package.sources["runner"]
    with package.use() as module:
        runner = module("runner").runner
        panel = module("target").target.panel
        assert panel.defer_updates is False
        assert runner(flag=True).out is False
        assert panel.defer_updates is True
        assert runner(flag=False).out is True
        assert panel.defer_updates is False


def test_defer_panel_updates_tells_the_views_of_the_panel(tmp_path: Path) -> None:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", ordered_target)
    runner_key = project.add_vi("Runner", defer_vi(target_key, "w"))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        panel = module("target").target.panel
        told: list[bool] = []

        class Watcher(module("panels").PanelView):
            def defer_updates_changed(self, deferred: bool) -> None:
                told.append(deferred)

        panel.attach(Watcher())
        module("runner").runner(flag=True)
        module("runner").runner(flag=False)
        assert told == [True, False]


def test_the_view_holds_changes_back_while_updates_are_deferred() -> None:
    widgets = PanelWidgets()
    drawn: list[Any] = []
    widgets.add_indicator("x", drawn.append)
    widgets.indicator_changed("x", 1)
    widgets.defer_updates_changed(True)
    widgets.paint()
    # NI: pending changes are redrawn when the deferral starts, then held back
    assert drawn == [1]
    widgets.indicator_changed("x", 2)
    widgets.indicator_changed("x", 3)
    widgets.paint()
    assert drawn == [1]
    widgets.defer_updates_changed(False)
    widgets.paint()
    assert drawn == [1, 3]
    widgets.indicator_changed("x", 4)
    widgets.paint()
    assert drawn == [1, 3, 4]


def test_a_view_built_while_updates_are_deferred_starts_deferred() -> None:
    panel = SimpleNamespace(defer_updates=True, holds=lambda name: False)
    widgets = PanelWidgets(panel=panel)
    drawn: list[Any] = []
    widgets.add_indicator("x", drawn.append)
    widgets.indicator_changed("x", 1)
    widgets.paint()
    assert drawn == []


def test_the_saved_bounds_are_the_heaps_origin_and_extent() -> None:
    def root(origin: str | None, pbounds: str | None) -> ET.Element:
        parts = ["<SL__rootObject>"]
        if origin is not None:
            parts.append(f"<origin>{origin}</origin>")
        if pbounds is not None:
            parts.append(f"<pBounds>{pbounds}</pBounds>")
        parts.append("</SL__rootObject>")
        return ET.fromstring("".join(parts))

    assert saved_window_bounds(root("(25, 94)", "(0, 0, 497, 657)")) == (
        25,
        94,
        682,
        591,
    )
    # the extent is the pBounds' size, wherever its corner is
    assert saved_window_bounds(root("(-1920, 40)", "(46, 0, 448, 680)")) == (
        -1920,
        40,
        -1240,
        442,
    )
    assert saved_window_bounds(root(None, "(0, 0, 497, 657)")) is None
    assert saved_window_bounds(root("(25, 94)", None)) is None
    assert saved_window_bounds(root("(25, 94)", "(0, 0, 0, 0)")) is None
