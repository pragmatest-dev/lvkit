"""Ctrl Val.Set (Deprecated) and Ctrl Val.Get All (Deprecated) on a VI reference
(``codegen/nodes/ctrl_val.py``): the value moves as flattened data and a type
string, through the panel's ``set_flattened`` and ``get_all_flattened``. Each case
is a handmade two-VI diagram whose generated package is imported and run."""

from __future__ import annotations

import struct
from pathlib import Path

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import ClusterField
from lvkit.type_string import encode
from tests.class_graphs import INT, Project, class_type, pane, wire
from tests.test_flat_class import pascal_name
from tests.test_front_panel_reference import (
    ERROR,
    FLAT_ENTRIES,
    GET_ALL_DEPRECATED,
    RUN,
    SET_DEPRECATED,
    VI_REF,
    Diagram,
    callee,
    chained,
    project_with,
    step,
)

INT_DESCRIPTOR = str(encode(INT))
FOUR = "\x00\x00\x00\x04"
FIVE = "\x00\x00\x00\x05"


def _steps(name: str) -> list[tuple]:
    return [
        step(
            "Ctrl Val.Set (Deprecated)",
            SET_DEPRECATED,
            name=name,
            flat=(INT_DESCRIPTOR, FOUR),
        ),
        step("Run VI", RUN),
        step("Ctrl Val.Get All (Deprecated)", GET_ALL_DEPRECATED),
    ]


def test_set_then_get_all_move_the_values_as_flattened_data(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    package = project_with(tmp_path, _steps("x"), "flat").generate(gaps)
    assert gaps == []
    assert "lvkit" not in package.sources["runner"]
    with package.use() as module:
        result = module("runner").runner()
        assert result.error_out is None
        # The indicator y is x + 1: its type descriptor and its big-endian data.
        assert result.out == [("y", list(encode(INT)), FIVE)]
        panel = module("target").target.panel
        assert panel.get("x") == 4
        assert panel.get_all_flattened(True) == [("x", list(encode(INT)), FOUR)]


def test_an_unknown_control_name_is_error_1(tmp_path: Path) -> None:
    package = project_with(tmp_path, _steps("nope"), "flat").generate()
    with package.use() as module:
        result = module("runner").runner()
        assert result.error_out.code == 1
        assert result.error_out.message == '"nope" not found'
        assert result.out == []


def test_a_reference_that_is_not_a_refnum_is_error_1026(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Target", callee)

    def runner(vi: str) -> tuple[VIContext, list[GraphNode]]:
        d = Diagram(vi)
        d.step(
            "set",
            "Ctrl Val.Set (Deprecated)",
            SET_DEPRECATED,
            name="x",
            flat=(INT_DESCRIPTOR, FOUR),
        )
        d.step("all", "Ctrl Val.Get All (Deprecated)", GET_ALL_DEPRECATED)
        d.wire("set.ref_out", "all.ref")
        d.wire("set.err_out", "all.err_in")
        d.wire("ref", "set.ref")
        error = pane(vi, "output", "err", ERROR, "error out", 1)
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "ref", VI_REF, "ref", 0)],
                outputs=[pane(vi, "output", "out", FLAT_ENTRIES, "out", 0), error],
                data_flow=[
                    *d.wires,
                    wire(vi, "all.result", "out"),
                    wire(vi, "all.err_out", "err"),
                ],
            ),
            d.nodes,
        )

    project.add_vi("Runner", runner)
    package = project.generate()
    with package.use() as module:
        run = module("runner").runner
        assert run(ref=module("target").target).error_out is None
        assert run(ref=None).error_out.code == 1026


def test_a_class_object_moves_as_flattened_data(tmp_path: Path) -> None:
    """A control of a class: its type descriptor is the class refnum the VI file
    stores, its data NI's flattened class object, read into the class by the
    package's dict."""
    cls = class_type("Cls")

    def take(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "c", cls, "c", 0)],
                outputs=[pane(vi, "output", "d", cls, "d", 1)],
                data_flow=[wire(vi, "c", "d")],
            ),
            [],
        )

    project = Project(tmp_path)
    project.add_class("Cls", [ClusterField(name="Count", type=INT)])
    target_key = project.add_vi("Target", take)
    descriptor = str(encode(cls))
    private = struct.pack(">i", 7)
    flat = (
        struct.pack(">I", 1)
        + pascal_name("Cls.lvclass")
        + struct.pack(">HHHH", 1, 0, 0, 0)
        + struct.pack(">i", len(private))
        + private
    ).decode("latin-1")

    project.add_vi(
        "Runner",
        chained(
            target_key,
            [
                step(
                    "Ctrl Val.Set (Deprecated)",
                    SET_DEPRECATED,
                    name="c",
                    flat=(descriptor, flat),
                ),
                step("Run VI", RUN),
                step("Ctrl Val.Get All (Deprecated)", GET_ALL_DEPRECATED),
            ],
            "flat",
        ),
    )
    project.graph._dep_graph.add_edge(  # noqa: SLF001
        project.vis["Runner"], target_key
    )
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        result = module("runner").runner()
        assert result.error_out is None
        assert result.out == [("d", list(encode(cls)), flat)]
        assert module("target").target.panel.get("c")._Cls__count == 7
