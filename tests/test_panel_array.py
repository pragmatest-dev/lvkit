"""One-dimensional array controls and indicators: a grid with a row for each
element. Each case is a tiny VI whose array control is wired to an array
indicator; the panel is generated, a row is added and edited as the browser
would, and a Run is checked in what the function took and what the indicator
shows."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing.user import User

import lvkit.panel.runtime as controls
from tests.error_diagrams import (  # noqa: F401
    BOOL,
    I32,
    REQUIRED,
    STR,
    Project,
    Vi,
    project,
)
from tests.panel_diagrams import (
    array_element,
    array_type,
    cluster_fields,
    cluster_type,
)
from tests.panel_harness import (
    build_in_a_page,
    fire,
    found,
    panel_of,
    run_once,
    simulate,
)

NAME = "Array Demo.vi"
STEM = "array_demo"


def _array_vi(
    element_class: str = "stdNum", element_type: int = I32, name: str = "Values"
) -> Vi:
    vi = Vi(NAME)
    kind = array_type(vi, element_type)
    element = array_element(vi, element_class, element_type)
    values = vi.control(
        name, kind, REQUIRED, ddo_class="indArr", bounds=(40, 20, 200, 200),
        inner=element,
    )
    shown = vi.indicator(
        "Shown", kind, ddo_class="indArr", bounds=(40, 300, 200, 480),
        inner=element,
    )
    vi.wire(values, shown)
    return vi


def _cluster_array_vi(fields: list[tuple[str, str, int]]) -> Vi:
    vi = Vi(NAME)
    row = cluster_type(vi, [type_id for _, _, type_id in fields])
    kind = array_type(vi, row)
    element = array_element(vi, "stdClust", row, cluster_fields(vi, fields))
    rows = vi.control(
        "Rows", kind, REQUIRED, ddo_class="indArr", bounds=(40, 20, 200, 300),
        inner=element,
    )
    shown = vi.indicator(
        "Shown", kind, ddo_class="indArr", bounds=(40, 340, 200, 620),
        inner=element,
    )
    vi.wire(rows, shown)
    return vi


def _record_calls(
    monkeypatch: pytest.MonkeyPatch, panel: Any
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    original = panel.vi_function

    def recorded(**arguments: Any) -> Any:
        calls.append(arguments)
        return original(**arguments)

    monkeypatch.setattr(panel, "vi_function", recorded)
    return calls


def test_an_array_of_numbers_takes_added_and_edited_rows_and_the_indicator_shows_them(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_array_vi())
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    assert "def array_demo(values: list[int] | None)" in package.source
    panel = panel_of(package, STEM)
    calls = _record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        control, indicator = found(user, ui.aggrid)
        user.find(marker="add-row").click()
        user.find(marker="add-row").click()
        assert control.options["rowData"] == [{"value": 0}, {"value": 0}]
        fire(control, "cellValueChanged", {"rowIndex": 1, "data": {"value": 7}})
        await run_once(user)
        assert calls[0]["values"] == [0, 7]
        assert indicator.options["rowData"] == [{"value": 0}, {"value": 7}]

    simulate(panel.build_panel, scenario)


def test_removing_a_row_takes_it_out_of_the_value(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_array_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)
    calls = _record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        control, indicator = found(user, ui.aggrid)
        for _ in range(3):
            user.find(marker="add-row").click()
        fire(control, "cellValueChanged", {"rowIndex": 0, "data": {"value": 1}})
        fire(control, "cellValueChanged", {"rowIndex": 1, "data": {"value": 2}})
        fire(control, "cellValueChanged", {"rowIndex": 2, "data": {"value": 3}})
        fire(control, "cellClicked", {"rowIndex": 1, "colId": "remove"})
        await run_once(user)
        assert calls[0]["values"] == [1, 3]
        assert indicator.options["rowData"] == [{"value": 1}, {"value": 3}]

    simulate(panel.build_panel, scenario)


def test_an_array_indicator_has_no_add_button_and_no_remove_column(
    project: Project,  # noqa: F811
) -> None:
    project.add(_array_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        control, indicator = found(user, ui.aggrid)
        assert len(user.find(marker="add-row").elements) == 1
        assert [c["colId"] for c in control.options["columnDefs"][1:]] == [
            "value",
            "remove",
        ]
        assert [c["colId"] for c in indicator.options["columnDefs"][1:]] == ["value"]

    simulate(panel.build_panel, scenario)


def test_an_array_of_clusters_has_a_column_for_each_field_and_rows_are_tuples(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(
        _cluster_array_vi(
            [
                ("Gain", "stdNum", I32),
                ("On", "stdBool", BOOL),
                ("Name", "stdString", STR),
            ]
        )
    )
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, STEM)
    calls = _record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        control, indicator = found(user, ui.aggrid)
        assert [c["headerName"] for c in control.options["columnDefs"][1:4]] == [
            "Gain",
            "On",
            "Name",
        ]
        user.find(marker="add-row").click()
        assert control.options["rowData"] == [{"0": 0, "1": False, "2": ""}]
        row = {"0": 4, "1": True, "2": "pump"}
        fire(control, "cellValueChanged", {"rowIndex": 0, "data": row})
        await run_once(user)
        assert calls[0]["rows"] == [(4, True, "pump")]
        assert indicator.options["rowData"] == [row]

    simulate(panel.build_panel, scenario)


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        (
            [("Gain", "stdNum", I32), ("Level", "stdSlide", I32)],
            "the panel has no widget for a stdSlide control of type int "
            "(field 'Level' of the cluster) (element of the array)",
        ),
    ],
)
@pytest.mark.filterwarnings("ignore:coroutine 'Event.wait' was never awaited")
def test_an_array_of_clusters_with_a_field_the_panel_cannot_draw_is_a_gap(
    project: Project,  # noqa: F811
    fields: list[tuple[str, str, int]],
    reason: str,
) -> None:
    project.add(_cluster_array_vi(fields))
    package = project.generate(NAME, panel=True)
    assert [(g.kind, g.node_name) for g in package.gaps] == [
        ("unsupported_control", "Rows"),
        ("unsupported_control", "Shown"),
    ]
    assert package.gaps[0].reason == reason
    with pytest.raises(NotImplementedError, match="element of the array"):
        build_in_a_page(panel_of(package, STEM).build_panel)


def test_an_array_of_arrays_is_a_gap(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    inner = array_type(vi, I32)
    outer = array_type(vi, inner)
    element = array_element(
        vi, "indArr", inner, array_element(vi, "stdNum", I32)
    )
    vi.control(
        "Grid", outer, REQUIRED, ddo_class="indArr", bounds=(40, 20, 200, 200),
        inner=element,
    )
    project.add(vi)
    package = project.generate(NAME, panel=True)
    assert [(g.kind, g.node_name) for g in package.gaps] == [
        ("unsupported_control", "Grid")
    ]
    assert package.gaps[0].reason == (
        "the panel has no widget for an array of arrays"
    )


def test_an_enum_column_shows_labels_and_gives_the_item_values() -> None:
    items = (("Low", 0), ("Mid", 5), ("High", 9))
    element = controls.Member("value", "", controls.choice_port, items, 5)
    ports: list[Any] = []

    def build() -> None:
        holder = controls.LiveValues()
        ports.append(controls.array_control(holder, "levels", element, 3))

    async def scenario(user: User) -> None:
        (grid,) = found(user, ui.aggrid)
        user.find(marker="add-row").click()
        assert grid.options["rowData"] == [{"value": "Mid"}]
        fire(grid, "cellValueChanged", {"rowIndex": 0, "data": {"value": "High"}})
        assert ports[0].get() == [9]
        ports[0].show([0, 5])
        assert grid.options["rowData"] == [{"value": "Low"}, {"value": "Mid"}]

    simulate(build, scenario)


def test_a_path_column_gives_paths() -> None:


    element = controls.Member("value", "", controls.path_port, "")
    ports: list[Any] = []

    def build() -> None:
        ports.append(
            controls.array_control(controls.LiveValues(), "files", element, 3)
        )

    async def scenario(user: User) -> None:
        (grid,) = found(user, ui.aggrid)
        user.find(marker="add-row").click()
        fire(grid, "cellValueChanged", {"rowIndex": 0, "data": {"value": "/a/b"}})
        assert ports[0].get() == [Path("/a/b")]

    simulate(build, scenario)
