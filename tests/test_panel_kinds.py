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

from tests.error_diagrams import PATH, REQUIRED, Project, Vi, project  # noqa: F401
from tests.panel_harness import found, panel_of, run_once, simulate

NAME = "Kind Demo.vi"
STEM = "kind_demo"


def _path_vi() -> Vi:
    vi = Vi(NAME)
    folder = vi.control(
        "Folder", PATH, REQUIRED, ddo_class="stdPath", bounds=(40, 20, 62, 260)
    )
    shown = vi.indicator(
        "Shown", PATH, ddo_class="stdPath", bounds=(40, 300, 62, 540)
    )
    vi.wire(folder, shown)
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


def test_a_path_control_passes_a_path_and_the_indicator_shows_its_text(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_path_vi())
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, STEM)
    calls = _record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        folder, shown = found(user, ui.input)
        folder.set_value("/data/run 1")
        await run_once(user)
        assert calls[0]["folder"] == Path("/data/run 1")
        assert isinstance(calls[0]["folder"], Path)
        assert shown.value == "/data/run 1"

    simulate(panel.build_panel, scenario)


def test_an_empty_path_control_passes_the_empty_path_and_shows_no_text(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_path_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)
    calls = _record_calls(monkeypatch, panel)

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
