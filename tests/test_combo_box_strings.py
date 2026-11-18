"""``Strings[]`` of a Combo Box reference (``codegen/nodes/panel_reference.py``,
``reference_properties`` of ``control_properties.json``): a read gives the last
write, else the strings the control is saved with; a write is held by the panel and
told to its views. Each case is a handmade diagram whose generated package is
imported and run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import FPTerminal, LVType, LVTypeKind
from tests.class_graphs import INT, Project, pane, wire
from tests.test_control_references import REFNUM, property_node
from tests.test_front_panel_reference import ERROR, STRING

STRINGS_CODE = (104099843,)
RING_STRINGS_CODE = (104056839,)
STRINGS = LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=STRING)


def combo(vi: str, saved: list[str]) -> FPTerminal:
    """The Combo Box control ``c`` of ``vi``, saved with the strings ``saved``."""
    terminal = pane(
        vi, "input", "c", STRING, "c", 0, tab_index=0, control_type="stdComboBox"
    )
    return terminal.model_copy(update={"enum_values": saved})


def target(saved: list[str]) -> Any:
    """A VI with the Combo Box ``c`` (saved with ``saved``), the number ``n`` and the
    indicator ``out``."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        return (
            VIContext(
                name=vi,
                inputs=[
                    combo(vi, saved),
                    pane(vi, "input", "n", INT, "n", 1, tab_index=1),
                ],
                outputs=[pane(vi, "output", "out", STRING, "out", 2, tab_index=2)],
                data_flow=[wire(vi, "c", "out")],
            ),
            [],
        )

    return build


def reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that reads the Strings[] of the Combo Box reference it is given."""
    node = property_node(
        vi, "ctl", "CmbBox", [("strings", STRINGS_CODE, "output", STRINGS)]
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                pane(vi, "output", "strings", STRINGS, "strings", 1),
                pane(vi, "output", "err", ERROR, "error out", 2),
            ],
            data_flow=[
                wire(vi, "ctl", "ctl.ref"),
                wire(vi, "ctl.strings", "strings"),
                wire(vi, "ctl.err_out", "err"),
            ],
        ),
        [node],
    )


def writer(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that writes the Strings[] of the Combo Box reference it is given."""
    node = property_node(
        vi, "ctl", "CmbBox", [("strings", STRINGS_CODE, "input", STRINGS)]
    )
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                pane(vi, "input", "items", STRINGS, "items", 1),
            ],
            outputs=[pane(vi, "output", "err", ERROR, "error out", 2)],
            data_flow=[
                wire(vi, "ctl", "ctl.ref"),
                wire(vi, "items", "ctl.strings"),
                wire(vi, "ctl.err_out", "err"),
            ],
        ),
        [node],
    )


def project_with(tmp_path: Path, saved: list[str]) -> Project:
    project = Project(tmp_path)
    project.add_vi("Target", target(saved))
    project.add_vi("Reader", reader)
    project.add_vi("Writer", writer)
    return project


def by_label(module: Any) -> dict[str, Any]:
    return {c.label: c for c in module("target").target.panel.controls}


def test_a_read_gives_the_strings_the_combo_box_is_saved_with(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    package = project_with(tmp_path, ["red", "green"]).generate(gaps)
    assert gaps == []
    assert "'strings': ['red', 'green']" in package.sources["target"]
    assert ".get_property('strings')" in package.sources["reader"]
    with package.use() as module:
        result = module("reader").reader(ctl=by_label(module)["c"])
        assert result.error_out is None
        assert result.strings == ["red", "green"]


def test_a_combo_box_saved_with_no_strings_reads_an_empty_array(
    tmp_path: Path,
) -> None:
    package = project_with(tmp_path, []).generate()
    assert "'strings': []" in package.sources["target"]
    with package.use() as module:
        assert module("reader").reader(ctl=by_label(module)["c"]).strings == []


def test_a_write_is_what_the_next_read_gives_and_the_views_are_told(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    package = project_with(tmp_path, ["red"]).generate(gaps)
    assert gaps == []
    assert ".set_property('strings', " in package.sources["writer"]
    with package.use() as module:
        panel = module("target").target.panel
        told: list[tuple[str, str, Any]] = []

        class Recorder(module("panels").PanelView):
            def property_changed(self, name: str, prop: str, value: Any) -> None:
                told.append((name, prop, value))

        panel.attach(Recorder())
        combo_ref = by_label(module)["c"]
        written = module("writer").writer(ctl=combo_ref, items=["a", "b", "c"])
        assert written.error_out is None
        assert told == [("c", "strings", ["a", "b", "c"])]
        assert panel.property_of("c", "strings") == ["a", "b", "c"]
        assert module("reader").reader(ctl=combo_ref).strings == ["a", "b", "c"]
        # the panel holds a copy: changing the caller's list changes nothing
        items = ["x"]
        module("writer").writer(ctl=combo_ref, items=items)
        items.append("y")
        assert module("reader").reader(ctl=combo_ref).strings == ["x"]


def test_a_control_that_is_not_a_combo_box_has_no_strings(tmp_path: Path) -> None:
    package = project_with(tmp_path, []).generate()
    with package.use() as module:
        number = by_label(module)["n"]
        result = module("reader").reader(ctl=number)
        assert result.error_out.code == 1026
        assert module("writer").writer(ctl=None, items=["a"]).error_out.code == 1026


def test_the_strings_of_a_ring_reference_stay_a_gap(tmp_path: Path) -> None:
    """Only a Combo Box's Strings[] is read and written through the panel: a ring
    keeps the value each string stands for, which the panel does not hold."""

    def ring_reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = property_node(
            vi,
            "ctl",
            "Ring (strict)",
            [("strings", RING_STRINGS_CODE, "output", STRINGS)],
        )
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
                outputs=[pane(vi, "output", "strings", STRINGS, "strings", 1)],
                data_flow=[
                    wire(vi, "ctl", "ctl.ref"),
                    wire(vi, "ctl.strings", "strings"),
                ],
            ),
            [node],
        )

    project = Project(tmp_path)
    project.add_vi("Ring Reader", ring_reader)
    gaps: list[Gap] = []
    project.generate(gaps)
    assert [g.reason for g in gaps] == [
        "Property 'p104056839' on a Ring (strict) reference has no Python equivalent"
    ]
