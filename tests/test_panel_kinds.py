"""The kinds of control the panel builds besides numbers, Booleans and strings.
Each case is a tiny VI whose control is wired to an indicator of the same kind;
the panel is generated, run headless and the painted value is read back."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing.user import User

from tests.error_diagrams import (  # noqa: F401
    BOOL,
    I32,
    PATH,
    REQUIRED,
    STR,
    Project,
    Vi,
    project,
)
from tests.panel_diagrams import cluster_fields, cluster_type
from tests.panel_harness import (
    build_in_a_page,
    controls_of,
    found,
    panel_of,
    record_calls,
    run_once,
    simulate,
)

NAME = "Kind Demo.vi"
STEM = "kind_demo"


def _path_vi() -> Vi:
    vi = Vi(NAME)
    folder = vi.control(
        "Folder", PATH, REQUIRED, ddo_class="stdPath", bounds=(40, 20, 62, 260)
    )
    shown = vi.indicator("Shown", PATH, ddo_class="stdPath", bounds=(40, 300, 62, 540))
    vi.wire(folder, shown)
    return vi


def _enum_vi() -> Vi:
    vi = Vi(NAME)
    kind = vi.add_type(
        '<TypeDesc Type="EnumU16"><EnumLabel>Low</EnumLabel>'
        "<EnumLabel>Mid</EnumLabel><EnumLabel>High</EnumLabel></TypeDesc>"
    )
    level = vi.control(
        "Level", kind, REQUIRED, ddo_class="stdEnum", bounds=(40, 20, 62, 160)
    )
    shown = vi.indicator("Shown", kind, ddo_class="stdEnum", bounds=(40, 300, 62, 440))
    vi.wire(level, shown)
    return vi


def _ring_vi(values: list[int] | None = None) -> Vi:
    """A ring ``Gain`` of three items, numbered 0, 1, 2 or by ``values``."""
    vi = Vi(NAME)
    kind = vi.add_type('<TypeDesc Type="UnitUInt16" />')
    sparse = "".join(
        f"<SL__arrayElement>{value.to_bytes(2, 'big', signed=True).hex()}"
        "</SL__arrayElement>"
        for value in values or []
    )
    ring = (
        '<SL__arrayElement class="multiLabel" uid="9001">'
        '<buf>(3)"Low""Mid""High"</buf></SL__arrayElement>'
        + (f"<ringSparseValues>{sparse}</ringSparseValues>" if values else "")
    )
    gain = vi.control(
        "Gain",
        kind,
        REQUIRED,
        ddo_class="stdRing",
        bounds=(40, 20, 62, 160),
        inner=ring,
    )
    shown = vi.indicator(
        "Shown", kind, ddo_class="stdRing", bounds=(40, 300, 62, 440), inner=ring
    )
    vi.wire(gain, shown)
    return vi


def test_a_path_control_passes_a_path_and_the_indicator_shows_its_text(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_path_vi())
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, STEM)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        folder, shown = found(user, ui.input)
        folder.set_value("/data/run 1")
        await run_once(user)
        assert calls[0]["folder"] == Path("/data/run 1")
        assert isinstance(calls[0]["folder"], Path)
        # The indicator shows the path in the platform's own separators.
        assert shown.value == str(Path("/data/run 1"))

    simulate(panel.build_panel, scenario)


def test_an_empty_path_control_passes_the_empty_path_and_shows_no_text(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_path_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        _, shown = found(user, ui.input)
        await run_once(user)
        assert calls[0]["folder"] == Path("")
        assert shown.value == ""

    simulate(panel.build_panel, scenario)


def test_the_path_indicator_has_no_browse_button_and_the_control_has_one(
    project: Project,  # noqa: F811
) -> None:
    project.add(_path_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        assert len(user.find(marker="browse").elements) == 1

    simulate(panel.build_panel, scenario)


def test_browse_lists_the_folder_on_the_server_and_selecting_fills_the_field(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    (tmp_path / "result.csv").write_text("x")
    project.add(_path_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        folder, _ = found(user, ui.input)
        folder.set_value(str(tmp_path))
        user.find(marker="browse").click()
        await asyncio.sleep(0.3)
        grid = found(user, ui.aggrid)[0]
        names = " ".join(row["name"] for row in grid.options["rowData"])
        assert "result.csv" in names
        assert str(tmp_path) == grid.options["columnDefs"][0]["headerName"]

    simulate(panel.build_panel, scenario)


def _selects(user: User) -> tuple[Any, Any]:
    control, indicator = found(user, ui.select)
    return control, indicator


def test_an_enum_control_offers_the_items_of_its_type_and_passes_the_item_number(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_enum_vi())
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, STEM)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        control, indicator = _selects(user)
        assert control.options == {0: "Low", 1: "Mid", 2: "High"}
        assert control.value == 0
        control.set_value(2)
        await run_once(user)
        assert calls[0]["level"] == 2
        assert type(calls[0]["level"]) is int
        assert indicator.value == 2

    simulate(panel.build_panel, scenario)


def test_a_ring_control_passes_the_value_the_ring_stores_for_the_item(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_ring_vi([0, 1024, 8192]))
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, STEM)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        control, indicator = _selects(user)
        assert control.options == {0: "Low", 1024: "Mid", 8192: "High"}
        control.set_value(1024)
        await run_once(user)
        assert calls[0]["gain"] == 1024
        assert indicator.value == 1024

    simulate(panel.build_panel, scenario)


def test_a_ring_numbered_from_zero_passes_the_item_number(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_ring_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        control, indicator = _selects(user)
        assert control.options == {0: "Low", 1: "Mid", 2: "High"}
        control.set_value(1)
        await run_once(user)
        assert calls[0]["gain"] == 1
        assert indicator.value == 1

    simulate(panel.build_panel, scenario)


def test_an_enum_indicator_shows_a_value_that_is_not_one_of_its_items(
    project: Project,  # noqa: F811
) -> None:
    project.add(_enum_vi())
    package = project.generate(NAME, panel=True)
    controls = controls_of(package)
    ports: list[Any] = []

    def build() -> None:
        holder = controls.PanelWidgets()
        ports.append(
            controls.choice_indicator(holder, "level", (("Low", 0), ("High", 1)), 0)
        )

    async def scenario(user: User) -> None:
        ports[0].show(7)
        (indicator,) = found(user, ui.select)
        assert indicator.value == 7
        assert indicator.options[7] == "7"

    simulate(build, scenario)


def test_an_enum_with_no_stored_items_is_a_gap_the_panel_raises_for(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    kind = vi.add_type('<TypeDesc Type="UnitUInt16" />')
    vi.control("Mode", kind, REQUIRED, ddo_class="stdEnum", bounds=(40, 20, 62, 160))
    project.add(vi)
    package = project.generate(NAME, panel=True)
    assert [(g.kind, g.node_name) for g in package.gaps] == [
        ("unsupported_control", "Mode")
    ]
    assert package.gaps[0].reason == (
        "the panel needs the items of the stdEnum control, and none are stored"
    )


def _cluster_vi(
    fields: list[tuple[str, str, int]] | None = None,
) -> Vi:
    """A cluster ``Point`` of a number, a Boolean and a string, wired to the
    indicator ``Shown`` of the same type."""
    fields = fields or [
        ("X", "stdNum", I32),
        ("Flag", "stdBool", BOOL),
        ("Note", "stdString", STR),
    ]
    vi = Vi(NAME)
    kind = cluster_type(vi, [type_id for _, _, type_id in fields])
    inner = cluster_fields(vi, fields)
    point = vi.control(
        "Point",
        kind,
        REQUIRED,
        ddo_class="stdClust",
        bounds=(40, 20, 180, 200),
        inner=inner,
    )
    shown = vi.indicator(
        "Shown", kind, ddo_class="stdClust", bounds=(40, 300, 180, 480), inner=inner
    )
    vi.wire(point, shown)
    return vi


def test_a_cluster_control_passes_its_fields_as_a_tuple_and_the_indicator_shows_them(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_cluster_vi())
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    assert "def kind_demo(point: tuple)" in package.source
    panel = panel_of(package, STEM)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        number = found(user, ui.number)[0]
        note, shown_number, shown_note = found(user, ui.input)
        flag = found(user, ui.switch)[0]
        number.set_value(5)
        flag.set_value(True)
        note.set_value("hi")
        await run_once(user)
        assert calls[0]["point"] == (5, True, "hi")
        assert shown_number.value == "5"
        assert shown_note.value == "hi"

    simulate(panel.build_panel, scenario)


@pytest.mark.filterwarnings("ignore:coroutine 'Event.wait' was never awaited")
def test_a_cluster_with_a_field_the_panel_has_no_widget_for_is_one_gap(
    project: Project,  # noqa: F811
) -> None:
    project.add(_cluster_vi([("X", "stdNum", I32), ("Level", "stdSlide", I32)]))
    package = project.generate(NAME, panel=True)
    assert [(g.kind, g.node_name) for g in package.gaps] == [
        ("unsupported_control", "Point"),
        ("unsupported_control", "Shown"),
    ]
    assert package.gaps[0].reason == (
        "the panel has no widget for a stdSlide control of type int "
        "(field 'Level' of the cluster)"
    )
    panel = panel_of(package, STEM)
    with pytest.raises(NotImplementedError, match="field 'Level' of the cluster"):
        build_in_a_page(panel.build_panel)
