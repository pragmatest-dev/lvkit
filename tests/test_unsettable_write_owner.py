"""The gap for a write of a row NI says "cannot be set while the VI runs"
(``codegen/nodes/unsettable_write.py``): NI's rule is about the VI that owns the
control, so it is quoted for a control of this VI and, for a control on another VI's
front panel reached through a VI reference, the gap says that is editing a VI. Each
case is a handmade diagram generated end to end."""

from __future__ import annotations

from pathlib import Path

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import (
    Constant,
    GraphNode,
    LocalVariableNode,
    PrimitiveNode,
    VIContext,
)
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.path_value import format_path
from tests.class_graphs import Project, pane, wire
from tests.test_control_references import (
    CONTROLS,
    ERROR,
    LABEL_TEXT,
    PANEL,
    REFNUM,
    REFNUMS,
    STRING,
    VALUE,
    ordered_target,
    property_node,
)
from tests.test_enum_strings import enum_control
from tests.vi_reference_diagrams import this_vi_type

VALUE_ROW = VALUE
LABEL_PAGE = (
    "https://docs-be.ni.com/api/bundle/labview-api-ref/page/properties-and-methods/"
    "vi-server/generic/gobject/decoration/text/text.html"
)
LABEL_ROW = "p104013829.p103995392"
EDITING = (
    "that is editing a VI (VI Scripting), which converted code has no equivalent for"
)
PATH = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Path")


def write_labels(
    vi: str,
    source: list[GraphNode],
    wires: list,
    inputs: list,
    row: tuple[str, tuple[int, ...]] = ("text", LABEL_TEXT),
):
    """A VI whose Controls[] of a Panel reference (made by ``source``, wired into
    the Panel node's ``ref``) are written to a Label.Text."""
    panel = property_node(vi, "panel", "VI", [("panel", PANEL, "output", REFNUM)])
    controls = property_node(
        vi, "controls", "Pnl", [("controls", CONTROLS, "output", REFNUMS)]
    )
    label = property_node(vi, "label", "Ctl", [(row[0], row[1], "input", STRING)])
    return (
        VIContext(
            name=vi,
            inputs=[*inputs, pane(vi, "input", "text", STRING, "text", 5)],
            outputs=[pane(vi, "output", "err", ERROR, "error out", 6)],
            data_flow=[
                *wires,
                wire(vi, "panel.panel", "controls.ref"),
                wire(vi, "controls.controls", "label.ref"),
                wire(vi, "text", f"label.{row[0]}"),
                wire(vi, "label.err_out", "err"),
            ],
        ),
        [*source, panel, controls, label],
    )


def this_vi_constant(vi: str) -> LocalVariableNode:
    return LocalVariableNode(
        id=f"{vi}:thisvi",
        vi_path=vi,
        name="This VI",
        node_type="ctlRefConst",
        control_name="This VI",
        terminals=[
            Terminal(
                id=f"{vi}:thisvi",
                index=0,
                direction="output",
                name="This VI",
                lv_type=this_vi_type(),
            )
        ],
    )


def open_vi_reference(vi: str) -> PrimitiveNode:
    return PrimitiveNode(
        id=f"{vi}:open",
        vi_path=vi,
        name="Open VI Reference",
        node_type="prim",
        prim_id=8010,
        terminals=[
            Terminal(id=f"{vi}:open.path", index=10, direction="input", lv_type=PATH),
            Terminal(
                id=f"{vi}:open.ref", index=3, direction="output", lv_type=this_vi_type()
            ),
        ],
    )


def reasons_of(project: Project, wanted: str) -> list[str]:
    gaps: list[Gap] = []
    project.generate(gaps)
    return [g.reason for g in gaps if g.reason.startswith(wanted)]


def own_message(row: str = LABEL_ROW) -> str:
    return (
        f"Property '{row}' on a Ctl reference is written, and NI's page says it "
        f"cannot be set while the VI runs ({LABEL_PAGE})"
    )


def other_message(row: str = LABEL_ROW) -> str:
    return (
        f"Property '{row}' on a Ctl reference is written on an object of another "
        f"VI's front panel, reached through a VI reference: {EDITING}"
    )


def test_a_write_through_the_panel_of_this_vi_quotes_nis_rule(tmp_path: Path) -> None:
    def build(vi: str):
        return write_labels(
            vi, [this_vi_constant(vi)], [wire(vi, "thisvi", "panel.ref")], []
        )

    project = Project(tmp_path)
    project.add_vi("Own", build)
    assert reasons_of(project, "Property 'p104013829") == [own_message()]


def test_a_write_through_an_unwired_vi_reference_is_this_vis_panel(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_vi("Own", lambda vi: write_labels(vi, [], [], []))
    assert reasons_of(project, "Property 'p104013829") == [own_message()]


def test_a_write_through_an_opened_vi_reference_is_editing_a_vi(
    tmp_path: Path,
) -> None:
    def build(vi: str):
        context, nodes = write_labels(
            vi, [open_vi_reference(vi)], [wire(vi, "path", "open.path")], []
        )
        context.constants = [
            Constant(
                id=f"{vi}:path", value=format_path(1, ["Target.vi"]), lv_type=PATH
            )
        ]
        context.data_flow.append(wire(vi, "open.ref", "panel.ref"))
        return context, nodes

    project = Project(tmp_path)
    project.add_vi("Target", ordered_target)
    project.add_vi("Opener", build)
    assert reasons_of(project, "Property 'p104013829") == [other_message()]


def test_a_write_through_a_vi_reference_input_is_editing_a_vi(tmp_path: Path) -> None:
    def build(vi: str):
        return write_labels(
            vi,
            [],
            [wire(vi, "vi", "panel.ref")],
            [pane(vi, "input", "vi", this_vi_type(), "vi", 0)],
        )

    project = Project(tmp_path)
    project.add_vi("Scripter", build)
    assert reasons_of(project, "Property 'p104013829") == [other_message()]


def test_a_control_reference_input_names_both_possibilities(tmp_path: Path) -> None:
    def build(vi: str):
        label = property_node(
            vi, "label", "Ctl", [("text", LABEL_TEXT, "input", STRING)]
        )
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                    pane(vi, "input", "text", STRING, "text", 1),
                ],
                outputs=[pane(vi, "output", "err", ERROR, "error out", 2)],
                data_flow=[
                    wire(vi, "ctl", "label.ref"),
                    wire(vi, "text", "label.text"),
                    wire(vi, "label.err_out", "err"),
                ],
            ),
            [label],
        )

    project = Project(tmp_path)
    project.add_vi("Renamer", build)
    assert reasons_of(project, "Property 'p104013829") == [
        f"Property '{LABEL_ROW}' on a Ctl reference is written: on an object of this "
        f"VI's own front panel, NI's page says it cannot be set while the VI runs "
        f"({LABEL_PAGE}); on an object of another VI's front panel reached through "
        f"a VI reference, {EDITING}"
    ]


def test_a_bound_property_node_of_this_vis_control_quotes_nis_rule(
    tmp_path: Path,
) -> None:
    def build(vi: str):
        node = property_node(
            vi, "p", "Ctl", [("text", LABEL_TEXT, "input", STRING)]
        ).model_copy(
            update={"bound_control_uid": "e-uid", "bound_control_dco_uid": "e-dco"}
        )
        return (
            VIContext(
                name=vi,
                inputs=[
                    enum_control(vi),
                    pane(vi, "input", "text", STRING, "text", 1),
                ],
                data_flow=[wire(vi, "text", "p.text")],
            ),
            [node],
        )

    project = Project(tmp_path)
    project.add_vi("Bound", build)
    assert reasons_of(project, "the Property Node of control 'e'") == [
        f"the Property Node of control 'e' writes '{LABEL_ROW}', which lvkit does "
        "not yet know how to carry out while the VI runs"
    ]


def test_a_settable_row_through_another_vis_panel_is_not_a_gap(
    tmp_path: Path,
) -> None:
    def build(vi: str):
        return write_labels(
            vi,
            [],
            [wire(vi, "vi", "panel.ref")],
            [pane(vi, "input", "vi", this_vi_type(), "vi", 0)],
            ("value", VALUE_ROW),
        )

    project = Project(tmp_path)
    project.add_vi("Scripter", build)
    assert reasons_of(project, "Property 'p1039") == []
