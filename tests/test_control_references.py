"""The references to a VI's front panel and to its controls
(``codegen/nodes/panel_reference.py``): the ``Panel`` property of a VI reference,
``Controls[]`` of the panel in tabbing order, and the ``Value``, ``Indicator`` and
``Label.Text`` of a control reference. Each case is a handmade diagram whose
generated package is imported and run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, PrimitiveNode, VIContext
from lvkit.models import LVType, LVTypeKind, PropertyDef, PropertySegment, Terminal
from tests.class_graphs import INT, Project, pane, wire
from tests.test_front_panel_reference import (
    BOOL,
    ERROR,
    GET,
    STRING,
    VARIANT,
    VI_REF,
    chained,
    static_reference,
    step,
)

REFNUM = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
REFNUMS = LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=REFNUM)

# The codes a file records (corpus census): the VI class's Panel, the Pnl class's
# Controls[], and a control reference's Label.Text, Indicator and Value.
PANEL = (573,)
CONTROLS = (104105985,)
LABEL_TEXT = (104013829, 103995392)
INDICATOR = (104013831,)
VALUE = (104013837,)

Row = tuple[str, tuple[int, ...], str, LVType]


def property_node(vi: str, tag: str, class_name: str, rows: list[Row]) -> PrimitiveNode:
    """A Property Node of ``class_name``: reference in 0 and out 1, error in 2 and
    out 3, then one terminal per row from 4 (``<tag>.<row name>``)."""
    fixed = [
        ("ref", 0, "input", REFNUM if class_name != "VI" else VI_REF),
        ("ref_out", 1, "output", REFNUM),
        ("err_in", 2, "input", ERROR),
        ("err_out", 3, "output", ERROR),
    ]
    terminals = [
        Terminal(id=f"{vi}:{tag}.{name}", index=index, direction=d, lv_type=t)
        for name, index, d, t in fixed
    ]
    for offset, (name, _, direction, lv_type) in enumerate(rows):
        terminals.append(
            Terminal(
                id=f"{vi}:{tag}.{name}",
                index=4 + offset,
                direction=direction,
                lv_type=lv_type,
            )
        )
    return PrimitiveNode(
        id=f"{vi}:{tag}",
        vi_path=vi,
        name="Property Node",
        node_type="propNode",
        object_name=class_name,
        terminals=terminals,
        properties=[
            PropertyDef(
                segments=[PropertySegment(name=f"p{code}", code=code) for code in codes]
            )
            for _, codes, _, _ in rows
        ],
        property_value_terminal_ids=[f"{vi}:{tag}.{name}" for name, *_ in rows],
    )


def ordered_target(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI whose panel is in tabbing order ``c``, ``b``, ``a``, not the order of
    its parameters: the controls ``a`` and ``b``, the indicator ``c``."""
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "a", INT, "a", 0, tab_index=2),
                pane(vi, "input", "b", INT, "b", 1, tab_index=1),
            ],
            outputs=[pane(vi, "output", "c", INT, "c", 2, tab_index=0)],
            data_flow=[wire(vi, "a", "c")],
        ),
        [],
    )


def controls_of_reference(target_key: str) -> Any:
    """A VI that returns the Controls[] of the Panel of a static reference."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        panel = property_node(vi, "panel", "VI", [("panel", PANEL, "output", REFNUM)])
        controls = property_node(
            vi, "controls", "Pnl", [("controls", CONTROLS, "output", REFNUMS)]
        )
        return (
            VIContext(
                name=vi,
                outputs=[
                    pane(vi, "output", "out", REFNUMS, "out", 0),
                    pane(vi, "output", "err", ERROR, "error out", 1),
                ],
                data_flow=[
                    wire(vi, "ref", "panel.ref"),
                    wire(vi, "panel.panel", "controls.ref"),
                    wire(vi, "panel.err_out", "controls.err_in"),
                    wire(vi, "controls.controls", "out"),
                    wire(vi, "controls.err_out", "err"),
                ],
            ),
            [static_reference(vi, target_key), panel, controls],
        )

    return build


def reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that reads the Label.Text, Indicator and Value of the control
    reference it is given."""
    node = property_node(
        vi,
        "ctl",
        "Ctl",
        [
            ("label", LABEL_TEXT, "output", STRING),
            ("indicator", INDICATOR, "output", BOOL),
            ("value", VALUE, "output", VARIANT),
        ],
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                pane(vi, "output", "label", STRING, "label", 1),
                pane(vi, "output", "indicator", BOOL, "indicator", 2),
                pane(vi, "output", "value", VARIANT, "value", 3),
                pane(vi, "output", "err", ERROR, "error out", 4),
            ],
            data_flow=[
                wire(vi, "ctl", "ctl.ref"),
                wire(vi, "ctl.label", "label"),
                wire(vi, "ctl.indicator", "indicator"),
                wire(vi, "ctl.value", "value"),
                wire(vi, "ctl.err_out", "err"),
            ],
        ),
        [node],
    )


def writer(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that writes the Value of the control reference it is given."""
    node = property_node(vi, "ctl", "Ctl", [("value", VALUE, "input", VARIANT)])
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                pane(vi, "input", "v", VARIANT, "v", 1),
            ],
            outputs=[pane(vi, "output", "err", ERROR, "error out", 2)],
            data_flow=[
                wire(vi, "ctl", "ctl.ref"),
                wire(vi, "v", "ctl.value"),
                wire(vi, "ctl.err_out", "err"),
            ],
        ),
        [node],
    )


def project_with_target(tmp_path: Path) -> tuple[Project, str]:
    project = Project(tmp_path)
    target_key = project.add_vi("Target", ordered_target)
    return project, target_key


def test_controls_of_the_panel_are_in_tabbing_order(tmp_path: Path) -> None:
    project, target_key = project_with_target(tmp_path)
    runner_key = project.add_vi("Runner", controls_of_reference(target_key))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    assert "order=['c', 'b', 'a']" in package.sources["target"]
    assert "panel_of(" in package.sources["runner"]
    assert ".controls" in package.sources["runner"]
    with package.use() as module:
        result = module("runner").runner()
        assert result.error_out is None
        assert [(c.label, c.indicator) for c in result.out] == [
            ("c", True),
            ("b", False),
            ("a", False),
        ]
        # the same objects each time, so two reads compare equal
        assert module("runner").runner().out == result.out


def test_a_control_reference_reads_its_label_indicator_and_value(
    tmp_path: Path,
) -> None:
    project, target_key = project_with_target(tmp_path)
    project.add_vi("Reader", reader)
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    with package.use() as module:
        panel = module("target").target.panel
        panel.set("a", 4)
        by_label = {c.label: c for c in panel.controls}
        variant, i32 = module("variants").Variant, module("variants").I32
        result = module("reader").reader(ctl=by_label["a"])
        # The Value property is a variant: the control's value and its type.
        assert (result.label, result.indicator, result.value) == (
            "a",
            False,
            variant(4, i32),
        )
        assert result.error_out is None
        result = module("reader").reader(ctl=by_label["c"])
        assert (result.label, result.indicator, result.value) == (
            "c",
            True,
            variant(None, i32),
        )


def test_the_value_of_a_control_reference_is_the_panels_value(
    tmp_path: Path,
) -> None:
    """A write through the reference is what Ctrl Val.Get reads back."""
    project, target_key = project_with_target(tmp_path)
    project.add_vi("Writer", writer)
    runner_key = project.add_vi(
        "Runner", chained(target_key, [step("Ctrl Val.Get", GET, name="c")], "value")
    )
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    gaps: list[Gap] = []
    package = project.generate(gaps)
    assert gaps == []
    assert ".variant = " in package.sources["writer"]
    with package.use() as module:
        panel = module("target").target.panel
        variant, i32 = module("variants").Variant, module("variants").I32
        first_indicator = next(c for c in panel.controls if c.indicator)
        written = module("writer").writer(ctl=first_indicator, v=variant(7, i32))
        assert written.error_out is None
        assert panel.get("c") == 7
        assert module("runner").runner().out == variant(7, i32)
        # and a Ctrl Val.Set is what the reference reads
        panel.set("a", 3)
        assert next(c for c in panel.controls if c.label == "a").value == 3


def test_a_reference_that_is_not_a_refnum_is_error_1026_on_error_out(
    tmp_path: Path,
) -> None:
    project, target_key = project_with_target(tmp_path)
    project.add_vi("Reader", reader)
    project.add_vi("Writer", writer)
    package = project.generate()
    with package.use() as module:
        variant, i32 = module("variants").Variant, module("variants").I32
        result = module("reader").reader(ctl=None)
        assert result.error_out.code == 1026
        assert (result.label, result.indicator, result.value) == (
            "",
            False,
            variant(),
        )
        written = module("writer").writer(ctl=None, v=variant(1, i32))
        assert written.error_out.code == 1026


def test_the_panel_of_a_reference_that_is_not_a_vi_is_error_1026(
    tmp_path: Path,
) -> None:
    project, target_key = project_with_target(tmp_path)
    runner_key = project.add_vi("Runner", controls_of_reference(target_key))
    project.graph._dep_graph.add_edge(runner_key, target_key)  # noqa: SLF001
    with project.generate().use() as module:
        panels = module("panels")
        with pytest.raises(panels.PanelError) as raised:
            panels.as_panel(None)
        assert raised.value.code == 1026
        with pytest.raises(panels.PanelError) as raised:
            panels.as_control(object())
        assert raised.value.code == 1026


def test_writes_with_no_python_meaning_stay_gaps(tmp_path: Path) -> None:
    """A label's text (renaming a control) and the Indicator flag are not set
    while the VI runs."""

    def renamer(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = property_node(
            vi,
            "ctl",
            "Ctl",
            [
                ("label", LABEL_TEXT, "input", STRING),
                ("indicator", INDICATOR, "input", BOOL),
            ],
        )
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                    pane(vi, "input", "text", STRING, "text", 1),
                    pane(vi, "input", "flag", BOOL, "flag", 2),
                ],
                outputs=[pane(vi, "output", "err", ERROR, "error out", 3)],
                data_flow=[
                    wire(vi, "ctl", "ctl.ref"),
                    wire(vi, "text", "ctl.label"),
                    wire(vi, "flag", "ctl.indicator"),
                    wire(vi, "ctl.err_out", "err"),
                ],
            ),
            [node],
        )

    project = Project(tmp_path)
    project.add_vi("Renamer", renamer)
    gaps: list[Gap] = []
    project.generate(gaps)
    assert [g.reason for g in gaps] == [
        "Property 'p104013829.p103995392' on a Ctl reference has no Python equivalent"
    ]
