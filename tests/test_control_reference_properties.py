"""Properties of a control read and written on a control REFERENCE through the panel
(``reference_properties`` of ``control_properties.json``): a read gives the last
write, else the state the file saves, else the default the row lists; a property
whose saved value the conversion does not decode raises on a read before a write;
a write is held by the panel and told to its views. Each case is a handmade diagram
whose generated package is imported and run."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import LVType
from tests.class_graphs import INT, Project, pane, wire
from tests.test_control_references import REFNUM, property_node
from tests.test_front_panel_reference import BOOL, ERROR, STRING

VISIBLE = (104013824,)
KEY_FOCUS = (104013826,)
DISABLED = (104013885,)
SELECTION_START = (104093700, 103995395)
FILL_COLOR = (104072194,)
CLUSTER_COLOR = (104102913,)


def target(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI with the controls ``plain``, ``hidden`` (saved hidden) and ``greyed``
    (saved greyed out)."""
    plain = pane(vi, "input", "plain", STRING, "plain", 0, tab_index=0)
    hidden = pane(vi, "input", "hidden", STRING, "hidden", 1, tab_index=1)
    greyed = pane(vi, "input", "greyed", BOOL, "greyed", 2, tab_index=2)
    return (
        VIContext(
            name=vi,
            inputs=[
                plain,
                hidden.model_copy(update={"starts_hidden": True}),
                greyed.model_copy(update={"enable_state": 2}),
            ],
            outputs=[pane(vi, "output", "out", STRING, "out", 3, tab_index=3)],
            data_flow=[wire(vi, "plain", "out")],
        ),
        [],
    )


def reader(
    class_name: str, codes: tuple[int, ...], kind: LVType
) -> Callable[[str], tuple[VIContext, list[GraphNode]]]:
    """A VI that reads the property ``codes`` of the ``class_name`` reference it is
    given."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = property_node(vi, "ctl", class_name, [("p", codes, "output", kind)])
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
                outputs=[
                    pane(vi, "output", "value", kind, "value", 1),
                    pane(vi, "output", "err", ERROR, "error out", 2),
                ],
                data_flow=[
                    wire(vi, "ctl", "ctl.ref"),
                    wire(vi, "ctl.p", "value"),
                    wire(vi, "ctl.err_out", "err"),
                ],
            ),
            [node],
        )

    return build


def writer(
    class_name: str, codes: tuple[int, ...], kind: LVType
) -> Callable[[str], tuple[VIContext, list[GraphNode]]]:
    """A VI that writes the property ``codes`` of the ``class_name`` reference."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        node = property_node(vi, "ctl", class_name, [("p", codes, "input", kind)])
        return (
            VIContext(
                name=vi,
                inputs=[
                    pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                    pane(vi, "input", "new", kind, "new", 1),
                ],
                outputs=[pane(vi, "output", "err", ERROR, "error out", 2)],
                data_flow=[
                    wire(vi, "ctl", "ctl.ref"),
                    wire(vi, "new", "ctl.p"),
                    wire(vi, "ctl.err_out", "err"),
                ],
            ),
            [node],
        )

    return build


def generate(
    tmp_path: Path,
    class_name: str,
    codes: tuple[int, ...],
    kind: LVType,
    gaps: list[Gap] | None = None,
) -> Any:
    project = Project(tmp_path)
    project.add_vi("Target", target)
    project.add_vi("Reader", reader(class_name, codes, kind))
    project.add_vi("Writer", writer(class_name, codes, kind))
    return project.generate(gaps)


def controls(module: Any) -> dict[str, Any]:
    return {c.label: c for c in module("target").target.panel.controls}


def test_visible_reads_the_saved_state_then_the_last_write_and_tells_the_views(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    package = generate(tmp_path, "Str", VISIBLE, BOOL, gaps)
    assert gaps == []
    with package.use() as module:
        told: list[tuple[str, str, Any]] = []

        class Recorder(module("panels").PanelView):
            def property_changed(self, name: str, prop: str, value: Any) -> None:
                told.append((name, prop, value))

        module("target").target.panel.attach(Recorder())
        refs = controls(module)
        assert module("reader").reader(ctl=refs["plain"]).value is True
        assert module("reader").reader(ctl=refs["hidden"]).value is False
        assert module("writer").writer(ctl=refs["hidden"], new=True).error_out is None
        assert told == [("hidden", "visible", True)]
        assert module("reader").reader(ctl=refs["hidden"]).value is True


def test_disabled_reads_the_saved_state_and_key_focus_starts_false(
    tmp_path: Path,
) -> None:
    package = generate(tmp_path, "Bool", DISABLED, INT)
    with package.use() as module:
        refs = controls(module)
        assert module("reader").reader(ctl=refs["greyed"]).value == 2
        assert module("writer").writer(ctl=refs["greyed"], new=0).error_out is None
        assert module("reader").reader(ctl=refs["greyed"]).value == 0
    (tmp_path / "focus").mkdir()
    package = generate(tmp_path / "focus", "Str", KEY_FOCUS, BOOL)
    with package.use() as module:
        refs = controls(module)
        assert module("reader").reader(ctl=refs["plain"]).value is False
        module("writer").writer(ctl=refs["plain"], new=True)
        assert module("reader").reader(ctl=refs["plain"]).value is True


def test_selection_start_of_a_string_starts_at_zero(tmp_path: Path) -> None:
    package = generate(tmp_path, "Str", SELECTION_START, INT)
    with package.use() as module:
        refs = controls(module)
        assert module("reader").reader(ctl=refs["plain"]).value == 0
        module("writer").writer(ctl=refs["plain"], new=4)
        assert module("reader").reader(ctl=refs["plain"]).value == 4


def test_a_saved_value_the_conversion_does_not_decode_raises_until_written(
    tmp_path: Path,
) -> None:
    package = generate(tmp_path, "Slide", FILL_COLOR, INT)
    with package.use() as module:
        refs = controls(module)
        with pytest.raises(NotImplementedError, match="fill_color.*'plain'"):
            module("reader").reader(ctl=refs["plain"])
        written = module("writer").writer(ctl=refs["plain"], new=0xFF0000)
        assert written.error_out is None
        assert module("reader").reader(ctl=refs["plain"]).value == 0xFF0000


def test_the_value_of_a_picture_reference_is_the_controls_own_value(
    tmp_path: Path,
) -> None:
    gaps: list[Gap] = []
    package = generate(tmp_path, "Pict", (104044544,), STRING, gaps)
    assert gaps == []
    with package.use() as module:
        refs = controls(module)
        assert module("writer").writer(ctl=refs["plain"], new="img").error_out is None
        assert module("reader").reader(ctl=refs["plain"]).value == "img"


def test_a_property_with_no_nis_page_stays_a_gap(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    generate(tmp_path, "Clust", CLUSTER_COLOR, INT, gaps)
    assert gaps
