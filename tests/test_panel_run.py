"""The run model of a generated panel: Run Continuously, Pause, Abort and the
error banner, driven headless with NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing.user import User

from tests.error_diagrams import Package, Project, project  # noqa: F401
from tests.panel_diagrams import NAME, counting_vi
from tests.panel_harness import (
    controls_of,
    labels,
    panel_of,
    record_calls,
    simulate,
    until,
)

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
    calls = record_calls(monkeypatch, panel)

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


_EXIT_SCRIPT = """
import importlib
import sys
import time

sys.path.insert(0, {parent!r})
module = importlib.import_module({module!r})
panel = getattr(module, {function!r}).panel
panel.run(wait=False)
time.sleep(0.5)
panel.abort()
time.sleep(0.5)
print("aborted", panel.aborted, flush=True)
"""


def test_a_program_that_aborts_a_panel_run_exits(
    project: Project,  # noqa: F811
    tmp_path: Path,
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    script = tmp_path / "run_panel.py"
    script.write_text(
        _EXIT_SCRIPT.format(
            parent=str(package.root.parent),
            module=package.module.__name__,
            function=package.function.__name__,
        ),
        encoding="utf-8",
    )
    finished = subprocess.run(
        [sys.executable, "-I", str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert finished.stdout.strip() == "aborted True"


@dataclass
class _Error:
    status: bool
    code: int
    source: str
    message: str = ""


class _EndedPanel:
    """A panel whose run ends at once with ``error_out`` holding ``error_out``."""

    state = 1
    paused = False
    aborted = False
    views: list[Any]

    def __init__(self, error_out: _Error | None) -> None:
        self.error_out = error_out
        self.views = []

    def attach(self, view: Any) -> None:
        self.views.append(view)

    def has(self, label: str) -> bool:
        return label == "error out"

    def get(self, label: str) -> Any:
        return self.error_out

    def run(self, wait: bool = True) -> None:
        for view in self.views:
            view.run_started()
            view.run_finished(None)


def _banner_panel(controls: ModuleType, error_out: _Error | None) -> Callable[[], None]:
    def build() -> None:
        panel = _EndedPanel(error_out)
        values = controls.PanelWidgets(panel)
        panel.attach(values)
        controller = controls.RunController(
            values, abortable=False, error_label="error out"
        )
        controller.toolbar()
        ui.timer(0.1, values.paint)

    return build


def test_the_error_banner_shows_the_code_source_and_status_of_error_out(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    controls = controls_of(package)
    build = _banner_panel(controls, _Error(True, 7, "Open File"))

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

    simulate(_banner_panel(controls, _Error(False, 5, "x")), warns)

    async def clean(user: User) -> None:
        user.find(marker="Run").click()
        await until(user, "done")
        assert "Error" not in labels(user)

    simulate(_banner_panel(controls, None), clean)


def test_a_run_while_the_vi_runs_by_reference_is_refused_with_error_1000_and_shown(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    vi_panel = panel.vi_function.panel

    async def scenario(user: User) -> None:
        vi_panel.run(wait=False)  # by reference: the Stop control is off, it loops
        user.find(marker="Run").click()  # before the page has been told
        await until(user, "Error 1000 (error)")
        await until(user, "running")
        vi_panel.abort()
        await until(user, "aborted")

    simulate(panel.build_panel, scenario)


def test_a_by_reference_run_shows_in_the_toolbar_and_its_indicators_on_the_page(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    vi_panel = panel.vi_function.panel

    async def scenario(user: User) -> None:
        vi_panel.run(wait=False)
        await until(user, "running")
        await asyncio.sleep(0.5)
        assert _count(user) > 0  # the Count indicator the VI wrote while it ran
        user.find(marker="Abort").click()
        await until(user, "aborted")
        assert vi_panel.state == 1

    simulate(panel.build_panel, scenario)


def test_a_set_by_reference_reaches_the_control_on_the_page(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    vi_panel = panel.vi_function.panel

    async def scenario(user: User) -> None:
        vi_panel.set("Offset", 41)
        for _ in range(60):
            await asyncio.sleep(0.05)
            if any(e.value == 41 for e in user.find(kind=ui.number).elements):
                break
        assert [e.value for e in user.find(kind=ui.number).elements] == [41]
        # the user's change goes the other way, to the panel
        user.find(kind=ui.number).elements.pop().set_value(7)
        assert vi_panel.get("Offset") == 7

    simulate(panel.build_panel, scenario)


def test_a_run_from_the_toolbar_uses_the_values_set_by_reference(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    calls = record_calls(monkeypatch, panel)
    vi_panel = panel.vi_function.panel
    vi_panel.set("Stop", True)
    vi_panel.set("Offset", 5)

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await until(user, "done")
        assert (calls[0]["stop"], calls[0]["offset"]) == (True, 5)
        assert vi_panel.get("Count") == 6

    simulate(panel.build_panel, scenario)


def test_two_views_of_one_panel_both_see_a_set_from_another_thread_and_detach_stops_it(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    vi_panel = panel.vi_function.panel
    controls = controls_of(package)
    names = {"offset": "Offset"}

    first = controls.PanelWidgets(vi_panel, names)
    second = controls.PanelWidgets(vi_panel, names)
    shown: dict[str, list[Any]] = {"first": [], "second": []}
    first.add_indicator("offset", shown["first"].append)
    second.add_indicator("offset", shown["second"].append)
    vi_panel.attach(first, names)
    vi_panel.attach(second, names)
    worker = threading.Thread(target=lambda: vi_panel.set("Offset", 3))
    worker.start()
    worker.join()
    first.paint()
    second.paint()
    assert shown == {"first": [0, 3], "second": [0, 3]}
    vi_panel.detach(second)
    vi_panel.set("Offset", 4)
    first.paint()
    second.paint()
    assert shown == {"first": [0, 3, 4], "second": [0, 3]}
    vi_panel.detach(first)


def test_an_indicator_written_while_the_vi_runs_reaches_every_view(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    vi_panel = panel.vi_function.panel
    controls = controls_of(package)
    names = {"count": "Count"}
    views = [controls.PanelWidgets(vi_panel, names) for _ in range(2)]
    shown: list[list[Any]] = [[], []]
    for view, into in zip(views, shown, strict=True):
        view.add_indicator("count", into.append)
        vi_panel.attach(view, names)
    vi_panel.set("Stop", True)
    vi_panel.run()
    for view in views:
        view.paint()
        vi_panel.detach(view)
    assert shown == [[1], [1]]


def test_abort_and_pause_through_the_panel_stop_and_hold_a_headless_run(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    vi_panel = package.function.panel
    vi_panel.run(wait=False)
    time.sleep(0.5)
    assert vi_panel.state == 2
    vi_panel.pause()
    time.sleep(0.3)  # the pass in flight reaches its next point and waits
    held = vi_panel.get("Count")
    time.sleep(0.5)
    assert vi_panel.get("Count") == held
    assert vi_panel.paused
    vi_panel.resume()
    time.sleep(0.5)
    assert vi_panel.get("Count") > held
    vi_panel.pause()
    vi_panel.abort()  # an abort releases a paused run
    for _ in range(60):
        if vi_panel.state == 1:
            break
        time.sleep(0.05)
    assert vi_panel.state == 1
    assert vi_panel.aborted
    assert not vi_panel.paused
