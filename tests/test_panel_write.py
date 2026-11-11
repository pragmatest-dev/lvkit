"""``generate --panel`` writes ``<vi>_panel.py`` next to the VI's module and the
``controls`` helper package at the package root. Each case is a tiny diagram
(``tests/panel_diagrams.py``) generated, imported and, for the panel, built and
driven headless with NiceGUI's own user simulation."""

from __future__ import annotations

import ast
import asyncio
import importlib
import sys
from collections.abc import Awaitable, Callable
from types import ModuleType

import pytest
from nicegui import Client, ui
from nicegui.testing.user import User
from nicegui.testing.user_simulation import user_simulation

from lvkit.cli import main
from tests.error_diagrams import (  # noqa: F401
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,
)
from tests.panel_diagrams import NAME, counting_vi

STEM = "panel_demo"


def _panel(package: Package) -> ModuleType:
    return importlib.import_module(f"{package.module.__package__}.{STEM}_panel")


def _controls(package: Package) -> ModuleType:
    return importlib.import_module(f"{package.module.__package__}.controls")


def _enum(package: Package) -> type:
    return package.module.PanelDemoValue


def _simulate(
    build_panel: Callable[[], None], scenario: Callable[[User], Awaitable[None]]
) -> None:
    async def run() -> None:
        async with user_simulation(build_panel) as user:
            await user.open("/")
            await scenario(user)

    asyncio.run(run())


def test_the_panel_is_written_next_to_the_module_with_the_controls_package(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    assert (package.root / f"{STEM}_panel.py").exists()
    assert {p.name for p in (package.root / "controls").glob("*.py")} >= {
        "__init__.py",
        "live.py",
        "port.py",
        "runtoolbar.py",
        "widgets.py",
    }
    assert package.gaps == []


def test_no_panel_is_written_without_the_flag(project: Project) -> None:  # noqa: F811
    project.add(counting_vi())
    package = project.generate(NAME)
    assert not (package.root / f"{STEM}_panel.py").exists()
    assert not (package.root / "controls").exists()


def test_the_panel_imports_only_nicegui_the_controls_and_the_logic_module(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    source = (package.root / f"{STEM}_panel.py").read_text()
    compile(source, "panel", "exec")
    root = package.root.name
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module and not node.level
    }
    assert imported == {
        "__future__",
        "pathlib",
        "typing",
        "nicegui",
        f"{root}.controls",
        f"{root}.{STEM}",
    }
    assert f"from {root}.{STEM} import {STEM} as vi_function, PanelDemoValue" in source
    for helper in (package.root / "controls").glob("*.py"):
        text = helper.read_text()
        assert "lvkit" not in text.replace("``lvkit.panel.write``", "")


def test_controls_are_stacked_in_rows_and_columns_in_the_order_of_their_bounds(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    source = (package.root / f"{STEM}_panel.py").read_text()
    # Stop (40, 20, 70, 120) and Count (40, 160, 62, 300) share the first row,
    # Stop on the left; Offset (110, 20, 132, 160) is a second row.
    assert "position:absolute" not in source
    assert source.count("ui.row()") == 2
    assert source.count("ui.column()") == 4  # the panel and one for each of three
    stop, count, offset = (
        source.index(f"caption('{label}')") for label in ("Stop", "Count", "Offset")
    )
    assert stop < count < offset


def test_the_panel_builds_headless_with_a_widget_for_each_control(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi(with_text=True))
    package = project.generate(NAME, panel=True)
    panel = _panel(package)

    async def scenario(user: User) -> None:
        for label in ("Stop", "Offset", "Count", "Message", "Echo", "Ready"):
            await user.should_see(label)
        assert len(user.find(kind=ui.switch).elements) == 1
        assert len(user.find(kind=ui.number).elements) == 1
        assert len(user.find(kind=ui.icon).elements) >= 1

    _simulate(panel.build_panel, scenario)


def test_an_indicator_wired_to_a_control_in_the_loop_shows_each_re_read_value(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi(with_text=True))
    package = project.generate(NAME, panel=True)
    names = _enum(package)
    holder = _controls(package).LiveValues()
    shown: list[object] = []
    holder.add_indicator(names.ECHO, shown.append)
    seen: list[object] = []
    stops = 0
    texts = iter(["a", "b", "c"])

    def on_read(name: str, value: object) -> object:
        nonlocal stops
        if name == names.STOP:
            stops += 1
            return stops >= 3
        if name == names.MESSAGE:
            return next(texts)
        return value

    def on_update(name: str, value: object) -> None:
        if name == names.ECHO:
            seen.append(value)
        holder.update(name, value)

    result = package.function(False, 0, "", on_update=on_update, on_read=on_read)
    assert seen == ["a", "b", "c"]
    assert result.echo == "c"
    holder.paint()
    assert shown == ["c"]


def test_a_run_with_a_stop_after_three_reads_leaves_the_last_count_in_the_holder(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    names = _enum(package)
    holder = _controls(package).LiveValues()
    shown: list[object] = []
    holder.add_indicator(names.COUNT, shown.append)
    reads: list[str] = []

    def on_read(name: str, value: object) -> object:
        reads.append(name)
        if name == names.STOP:
            return reads.count(names.STOP) >= 3
        return value

    result = package.function(False, 0, on_update=holder.update, on_read=on_read)
    assert result.count == 3
    holder.paint()
    assert shown == [3]
    holder.paint()
    assert shown == [3]


def test_the_holder_keys_a_name_by_the_value_of_its_enum_member(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    names = _enum(package)
    holder = _controls(package).LiveValues()
    holder.add_control(names.OFFSET, 5)
    assert holder.read("offset", 0) == 5
    assert holder.read(names.OFFSET, 0) == 5
    assert holder.read(names.STOP, True) is True


def test_abort_ends_the_run_at_the_next_hook_call(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    names = _enum(package)
    controls = _controls(package)
    holder = controls.LiveValues()
    holder.request_abort()
    with pytest.raises(controls.Abort):
        package.function(False, 0, on_update=holder.update, on_read=holder.read)
    assert not issubclass(controls.Abort, Exception)
    holder.clear_abort()
    assert holder.read(names.STOP, True) is True


def test_run_calls_the_function_and_paints_the_result_when_it_returns(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await asyncio.sleep(0.35)
        await user.should_see("running")
        user.find(kind=ui.switch).click()
        for _ in range(30):
            await asyncio.sleep(0.1)
            if "done" in " ".join(
                str(e.text) for e in user.find(kind=ui.label).elements
            ):
                break
        await user.should_see("done")
        counted = [e.value for e in user.find(kind=ui.input).elements if e.value]
        assert counted and int(counted[0]) >= 3

    _simulate(panel.build_panel, scenario)


def test_abort_in_the_panel_ends_a_run_that_is_waiting_for_stop(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)

    async def scenario(user: User) -> None:
        user.find(marker="Run").click()
        await asyncio.sleep(0.35)
        user.find(marker="Abort").click()
        for _ in range(30):
            await asyncio.sleep(0.1)
            if "aborted" in " ".join(
                str(e.text) for e in user.find(kind=ui.label).elements
            ):
                break
        await user.should_see("aborted")

    _simulate(panel.build_panel, scenario)


def _slide_control_vi() -> Vi:
    vi = counting_vi()
    vi.control("Level", I32, REQUIRED, ddo_class="stdSlide", bounds=(250, 20, 272, 260))
    return vi


@pytest.mark.filterwarnings("ignore:coroutine 'Event.wait' was never awaited")
def test_a_control_the_panel_has_no_widget_for_is_a_gap_that_raises_when_built(
    project: Project,  # noqa: F811
) -> None:
    project.add(_slide_control_vi())
    package = project.generate(NAME, panel=True)
    assert [(g.kind, g.node_name) for g in package.gaps] == [
        ("unsupported_control", "Level")
    ]
    assert package.gaps[0].reason == (
        "the panel has no widget for a stdSlide control of type int"
    )
    source = (package.root / f"{STEM}_panel.py").read_text()
    assert "raise NotImplementedError(" in source
    panel = _panel(package)
    with pytest.raises(NotImplementedError, match="stdSlide control of type int"):
        _build_in_a_page(panel.build_panel)


def _build_in_a_page(build_panel: Callable[[], None]) -> None:
    async def run() -> None:
        async with user_simulation():
            with Client(ui.page("/gap")):
                build_panel()

    asyncio.run(run())


def _generate(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> int:
    monkeypatch.setattr(sys, "argv", ["lvkit", "generate", *arguments, "--panel"])
    return main()


def test_the_command_reports_a_panel_gap_as_a_partial_run(
    project: Project,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = project.add(_slide_control_vi())
    out = project.base / "cli"
    assert _generate(monkeypatch, str(path), "-o", str(out)) == 3
    captured = capsys.readouterr()
    assert "Level" in captured.err
    assert "unsupported_control" in captured.err
    assert (out / STEM / f"{STEM}_panel.py").exists()


def test_the_command_exits_0_for_a_panel_with_no_gaps(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = project.add(counting_vi())
    out = project.base / "cli"
    assert _generate(monkeypatch, str(path), "-o", str(out)) == 0
