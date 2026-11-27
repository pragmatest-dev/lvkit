"""A control the panel has no widget for is drawn as a placeholder with the reason
it is not converted, and the rest of the panel works: an input passes nothing of
its own (the run uses the control's default), an indicator keeps its value without
drawing it."""

from __future__ import annotations

import pytest
from nicegui import ui
from nicegui.testing.user import User

from tests.error_diagrams import (  # noqa: F401
    BOOL,
    I32,
    OPTIONAL,
    REQUIRED,
    Project,
    Vi,
    project,
)
from tests.panel_harness import (
    found,
    labels,
    panel_of,
    record_calls,
    run_once,
    simulate,
)

NAME = "Placeholder Demo.vi"
STEM = "placeholder_demo"


def _vi(rule: int) -> Vi:
    """A slide ``Level`` (a kind with no widget) wired to the slide indicator
    ``Gauge``, and a Boolean ``On`` wired to the Boolean indicator ``Shown``."""
    vi = Vi(NAME)
    level = vi.control(
        "Level", I32, rule, ddo_class="stdSlide", bounds=(40, 20, 62, 160)
    )
    gauge = vi.indicator("Gauge", I32, ddo_class="stdSlide", bounds=(40, 200, 62, 340))
    on = vi.control(
        "On", BOOL, REQUIRED, ddo_class="stdBool", bounds=(90, 20, 120, 120)
    )
    shown = vi.indicator("Shown", BOOL, ddo_class="stdBool", bounds=(90, 200, 120, 300))
    vi.wire(level, gauge)
    vi.wire(on, shown)
    return vi


def test_a_required_input_with_no_widget_runs_with_its_default(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_vi(REQUIRED))
    package = project.generate(NAME, panel=True)
    assert [(g.kind, g.node_name) for g in package.gaps] == [
        ("unsupported_control", "Level"),
        ("unsupported_control", "Gauge"),
    ]
    source = (package.root / f"{STEM}_panel.py").read_text(encoding="utf-8")
    assert "raise NotImplementedError" not in source
    panel = panel_of(package, STEM)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        text = labels(user)
        assert text.count("This control is not converted: ") == 2
        assert "the panel has no widget for a stdSlide control of type int" in text
        switch = found(user, ui.switch)[0]
        switch.set_value(True)
        await run_once(user)
        assert calls[0]["level"] == 0
        assert calls[0]["on"] is True
        assert panel.vi_function.panel.value("gauge") == 0

    simulate(panel.build_panel, scenario)


def test_an_optional_input_with_no_widget_is_not_passed(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_vi(OPTIONAL))
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)
    calls = record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        await run_once(user)
        assert "level" not in calls[0]
        assert calls[0]["on"] is False

    simulate(panel.build_panel, scenario)


def test_the_supported_controls_beside_a_placeholder_are_unchanged(
    project: Project,  # noqa: F811
) -> None:
    project.add(_vi(REQUIRED))
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        assert len(found(user, ui.switch)) >= 1  # the Boolean control On
        assert "On" in labels(user)

    simulate(panel.build_panel, scenario)
