"""The run model of a generated panel: Run Continuously, Pause, Abort and the
error banner, driven headless with NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing.user import User

from tests.error_diagrams import Package, Project, project  # noqa: F401
from tests.panel_diagrams import NAME, counting_vi
from tests.panel_harness import controls_of, labels, panel_of, simulate, until

STEM = "panel_demo"


def _panel(package: Package) -> ModuleType:
    return panel_of(package, STEM)


def _count(user: User) -> int:
    values = [e.value for e in user.find(kind=ui.input).elements if e.value]
    return int(values[0]) if values else 0


def test_run_continuously_runs_again_until_its_toggle_is_switched_off(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    calls: list[dict[str, Any]] = []
    original = panel.vi_function

    def counted(**arguments: Any) -> Any:
        calls.append(arguments)
        return original(**arguments)

    monkeypatch.setattr(panel, "vi_function", counted)

    async def scenario(user: User) -> None:
        user.find(kind=ui.switch).click()  # Stop: each run ends after one pass
        user.find(marker="Run-Continuously").click()
        for _ in range(100):
            await asyncio.sleep(0.05)
            if len(calls) >= 3:
                break
        assert len(calls) >= 3
        assert "running" in labels(user)
        user.find(marker="Run-Continuously").click()  # toggle off
        await until(user, "done")
        settled = len(calls)
        await asyncio.sleep(0.5)
        assert len(calls) == settled

    simulate(panel.build_panel, scenario)


def test_pause_holds_the_function_at_its_next_hook_call_and_resume_continues(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await asyncio.sleep(0.5)
        user.find(marker="Pause").click()
        await until(user, "paused")
        await asyncio.sleep(0.3)  # the pass in flight reaches its hook and waits
        held = _count(user)
        await asyncio.sleep(0.6)
        assert _count(user) == held
        user.find(marker="Pause").click()  # resume
        await asyncio.sleep(0.6)
        assert _count(user) > held
        user.find(marker="Abort").click()
        await until(user, "aborted")

    simulate(panel.build_panel, scenario)


def test_abort_during_pause_ends_the_run(project: Project) -> None:  # noqa: F811
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await asyncio.sleep(0.4)
        user.find(marker="Pause").click()
        await until(user, "paused")
        await asyncio.sleep(0.3)
        user.find(marker="Abort").click()
        await until(user, "aborted")

    simulate(panel.build_panel, scenario)


def test_abort_ends_run_continuously(project: Project) -> None:  # noqa: F811
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)

    async def scenario(user: User) -> None:
        user.find(marker="Run-Continuously").click()
        await asyncio.sleep(0.4)
        user.find(marker="Abort").click()
        await until(user, "aborted")

    simulate(panel.build_panel, scenario)


@dataclass
class _Error:
    status: bool
    code: int
    source: str
    message: str = ""


@dataclass
class _Result:
    error_out: _Error | None


def _banner_panel(controls: ModuleType, result: _Result) -> Callable[[], None]:
    def build() -> None:
        values = controls.LiveValues()
        controller = controls.RunController(
            values, lambda: result, lambda _: None, abortable=False
        )
        controller.toolbar()

    return build


def test_the_error_banner_shows_the_code_source_and_status_of_error_out(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    controls = controls_of(package)
    build = _banner_panel(controls, _Result(_Error(True, 7, "Open File")))

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await until(user, "Error 7 (error)")
        await user.should_see("source: Open File")

    simulate(build, scenario)


def test_a_warning_shows_in_the_banner_and_no_error_shows_none(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    controls = controls_of(package)

    async def warns(user: User) -> None:
        user.find(marker="Run").click()
        await until(user, "Error 5 (warning)")

    simulate(_banner_panel(controls, _Result(_Error(False, 5, "x"))), warns)

    async def clean(user: User) -> None:
        user.find(marker="Run").click()
        await until(user, "done")
        assert "Error" not in labels(user)

    simulate(_banner_panel(controls, _Result(None)), clean)
