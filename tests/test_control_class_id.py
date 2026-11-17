"""The ClassID of a control reference (``control_classes.json``): the number NI's
published table gives the control's VI Server class. Each case is a handmade
diagram whose generated package is imported and run."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.control_classes import class_ids, vi_server_class
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import LVType, LVTypeKind
from tests.class_graphs import INT, Project, pane, wire
from tests.test_control_references import REFNUM, property_node
from tests.test_front_panel_reference import ERROR

CLASS_ID = (103970816,)
U32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt32")
ENUM = LVType(kind=LVTypeKind.ENUM, underlying_type="NumUInt16")
RING = LVType(kind=LVTypeKind.RING, underlying_type="NumUInt16")


def class_id_reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that reads the ClassID of the control reference it is given."""
    node = property_node(vi, "ctl", "Ctl", [("class_id", CLASS_ID, "output", U32)])
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                pane(vi, "output", "id", U32, "id", 1),
                pane(vi, "output", "err", ERROR, "error out", 2),
            ],
            data_flow=[
                wire(vi, "ctl", "ctl.ref"),
                wire(vi, "ctl.class_id", "id"),
                wire(vi, "ctl.err_out", "err"),
            ],
        ),
        [node],
    )


def panel_target(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI whose panel has a numeric, an enum, a ring, a Boolean and a control of a
    front-panel class that has no VI Server class recorded."""
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "n", INT, "n", 0, control_type="stdNum"),
                pane(vi, "input", "e", ENUM, "e", 1, control_type="stdRing"),
                pane(vi, "input", "r", RING, "r", 2, control_type="stdRing"),
                pane(vi, "input", "u", INT, "u", 3, control_type="stdGraph"),
            ],
            outputs=[
                pane(vi, "output", "b", INT, "b", 4, control_type="stdBool"),
            ],
            data_flow=[wire(vi, "n", "b")],
        ),
        [],
    )


def test_class_names_come_from_the_front_panel_class_and_the_type() -> None:
    assert vi_server_class("stdNum", "primitive") == "Digital"
    assert vi_server_class("stdRing", "enum") == "Enum"
    assert vi_server_class("stdRing", "ring") == "Ring"
    assert vi_server_class("stdRing", None) is None
    assert vi_server_class("stdGraph", "primitive") is None
    assert vi_server_class(None, None) is None
    assert class_ids()["Enum"] == 35
    assert class_ids()["Digital"] == 18


def test_a_controls_class_is_a_field_of_its_graph_terminal() -> None:
    vi = "T"
    context, _ = panel_target(vi)
    found = {t.name: t.vi_server_class for t in [*context.inputs, *context.outputs]}
    assert found == {
        "n": "Digital",
        "e": "Enum",
        "r": "Ring",
        "u": None,
        "b": "Boolean",
    }


def test_the_class_id_of_a_control_reference_is_nis_number(tmp_path: Path) -> None:
    project = Project(tmp_path)
    project.add_vi("Target", panel_target)
    project.add_vi("Reader", class_id_reader)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    assert "classes={" in package.sources["target"]
    with package.use() as module:
        panel = module("target").target.panel
        by_label = {c.label: c for c in panel.controls}
        read = module("reader").reader
        results = {
            label: read(ctl=by_label[label]) for label in ("n", "e", "r", "b")
        }
        assert {label: r.id_ for label, r in results.items()} == {
            "n": 18,
            "e": 35,
            "r": 34,
            "b": 8,
        }
        assert all(r.error_out is None for r in results.values())
        # a control whose class has no row: error 1 on error out, naming it
        unmapped = read(ctl=by_label["u"])
        assert unmapped.error_out.code == 1
        assert '"u"' in unmapped.error_out.message
        # not a reference
        assert read(ctl=None).error_out.code == 1026


def test_a_class_name_nis_table_does_not_list_is_an_error_naming_it(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_vi("Target", panel_target)
    with project.generate().use() as module:
        panels = module("panels")
        control = panels.Control(
            module("target").target.panel, "x", "x", False, None, "Mystery"
        )
        with pytest.raises(panels.PanelError) as raised:
            control.class_id  # noqa: B018
        assert "'Mystery'" in raised.value.message
