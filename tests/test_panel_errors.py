"""Error cluster controls and indicators on a generated panel: a status light, a
code number and a source text, crossing to the function as ``None`` or the
package's ``LabVIEWError``."""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing.user import User

from tests.error_diagrams import (  # noqa: F401
    ERR,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,
)
from tests.panel_diagrams import NAME
from tests.panel_harness import (
    controls_of,
    found,
    panel_of,
    record_calls,
    run_once,
    simulate,
)

STEM = "panel_demo"
OFF = "text-green-500"
ON = "text-red-500"


def _passthrough() -> Vi:
    """``error in`` wired straight to ``error out``."""
    vi = Vi(NAME)
    ein = vi.control(
        "error in", ERR, REQUIRED, ddo_class="stdClust", bounds=(40, 20, 160, 220)
    )
    eout = vi.indicator(
        "error out", ERR, ddo_class="stdClust", bounds=(40, 260, 160, 460)
    )
    vi.wire(ein, eout)
    return vi


def _panel(package: Package) -> ModuleType:
    return panel_of(package, STEM)


def _light(user: User) -> str:
    return " ".join(found(user, ui.icon)[-1].classes)


def test_an_error_cluster_is_an_error_widget_not_a_gap(
    project: Project,  # noqa: F811
) -> None:
    project.add(_passthrough())
    package = project.generate(NAME, panel=True)
    assert [g for g in package.gaps if g.kind == "unsupported_control"] == []
    source = (package.root / f"{STEM}_panel.py").read_text()
    assert "error_control(values, 'error_in')" in source
    assert "error_indicator(values, 'error_out')" in source
    assert (package.root / "errors.py").exists()


def test_a_run_with_no_error_leaves_the_indicator_with_status_off(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_passthrough())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        await run_once(user)
        assert [c["error_in"] for c in calls] == [None]
        assert OFF in _light(user)
        numbers = found(user, ui.number)
        assert [n.value for n in numbers] == [0, 0]
        assert [i.value for i in found(user, ui.input)] == ["", ""]

    simulate(panel.build_panel, scenario)


def test_a_control_with_status_on_passes_a_labview_error_and_the_indicator_shows_it(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_passthrough())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        found(user, ui.switch)[0].set_value(True)
        found(user, ui.number)[0].set_value(5000)
        found(user, ui.input)[0].set_value("x")
        await run_once(user)
        (call,) = calls
        error = call["error_in"]
        assert isinstance(error, package.errors.LabVIEWError)
        assert (error.code, error.source, error.status) == (5000, "x", True)
        assert ON in _light(user)
        assert found(user, ui.number)[1].value == 5000
        assert found(user, ui.input)[1].value == "x"

    simulate(panel.build_panel, scenario)


def test_status_off_with_a_code_is_a_warning(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_passthrough())
    package = project.generate(NAME, panel=True)
    panel = _panel(package)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        found(user, ui.number)[0].set_value(1)
        await run_once(user)
        (call,) = calls
        error = call["error_in"]
        assert isinstance(error, package.errors.LabVIEWError)
        assert (error.code, error.status) == (1, False)
        assert OFF in _light(user)
        assert found(user, ui.number)[1].value == 1

    simulate(panel.build_panel, scenario)


def test_an_indicator_shows_the_message_of_the_error_it_is_given(
    project: Project,  # noqa: F811
) -> None:
    project.add(_passthrough())
    package = project.generate(NAME, panel=True)
    controls = controls_of(package)
    ports: list[Any] = []

    def build() -> None:
        ports.append(controls.error_port(readonly=True))

    async def scenario(user: User) -> None:
        (port,) = ports
        port.show(
            package.errors.LabVIEWError(code=7, source="s", message="bad", status=True)
        )
        shown = port.get()
        assert (shown.code, shown.source, shown.status) == (7, "s", True)
        await user.should_see("bad")
        port.show(None)
        assert port.get() is None

    simulate(build, scenario)
