"""Control properties on the generated panel: a VI sets Visible, DisabledEnum or
Maximum of its own controls while it runs and the NiceGUI page shows it; a control
the file stores hidden or greyed out starts that way; ``LiveIO.get_property`` gives
the function the panel's state of a property, and the held value with no panel; a
property a widget kind cannot show is a generator warning. Each case is a tiny
diagram (``tests/panel_diagrams.py``) generated, imported and, for the panel,
driven headless with NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing.user import User

import lvkit.panel.runtime as controls
from lvkit.codegen.gaps import UNSUPPORTED_CONTROL_PROPERTY
from lvkit.panel.kinds import SHOWN_PROPERTIES, PanelKind
from lvkit.panel.live_io import LiveIO
from tests.error_diagrams import (
    BOOL,
    I32,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.panel_diagrams import value_property
from tests.panel_harness import fire, found, panel_of, run_once, simulate

NAME = "Panel Demo.vi"
STEM = "panel_demo"

HIDDEN = 0x8
DISABLED = 0x200
GREYED_OUT = 0x80


def _box_visible(widget: Any) -> bool:
    """Whether the box a generated panel builds around a control and its caption
    is shown."""
    return bool(widget.parent_slot.parent.visible)


def _property_vi() -> Vi:
    """A number control ``n`` whose Maximum is set to 10 and which is disabled and
    greyed out, and a Boolean control ``flag`` that is hidden, all by the VI."""
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED, bounds=(40, 20, 62, 160))
    flag = vi.control(
        "flag", BOOL, REQUIRED, ddo_class="stdBool", bounds=(110, 20, 140, 120)
    )
    out = vi.indicator("out", I32, bounds=(40, 200, 62, 340))
    maximum, disabled = value_property(vi, n, ["Maximum", "DisabledEnum"])
    vi.wire(vi.int_constant(10), maximum)
    vi.wire(vi.int_constant(2), disabled)
    (visible,) = value_property(vi, flag, ["Visible"])
    vi.wire(vi.bool_constant(False), visible)
    vi.wire(n, out)
    return vi


def test_the_page_shows_the_properties_the_vi_sets(
    project: Project,  # noqa: F811
) -> None:
    project.add(_property_vi())
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        (number, _out) = found(user, ui.number) + found(user, ui.input)
        (switch,) = found(user, ui.switch)
        assert number.max == float("inf")
        assert number.enabled
        assert _box_visible(number) and _box_visible(switch)
        await run_once(user)
        await asyncio.sleep(0.4)
        assert number.max == 10.0
        assert not number.enabled
        assert "opacity-40" in number.classes
        assert not _box_visible(switch)
        assert _box_visible(number)

    simulate(panel.build_panel, scenario)


def test_a_disabled_state_of_1_disables_without_greying_and_0_enables(
    project: Project,  # noqa: F811
) -> None:
    project.add(_property_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)
    vi_panel = panel.vi_function.panel

    async def scenario(user: User) -> None:
        (number,) = found(user, ui.number)
        vi_panel._live_property("n", "disabled", 1)
        await asyncio.sleep(0.4)
        assert not number.enabled
        assert "opacity-40" not in number.classes
        vi_panel._live_property("n", "disabled", 0)
        await asyncio.sleep(0.4)
        assert number.enabled
        # Key Focus True and False reach the number box and the switch.
        for name in ("n", "flag"):
            vi_panel._live_property(name, "key_focus", True)
            vi_panel._live_property(name, "key_focus", False)
        await asyncio.sleep(0.4)
        assert vi_panel.property_of("flag", "key_focus") is False

    simulate(panel.build_panel, scenario)


def test_a_control_the_file_stores_hidden_and_greyed_out_starts_that_way(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control(
        "n",
        I32,
        REQUIRED,
        ddo_flags=HIDDEN | DISABLED | GREYED_OUT,
        bounds=(40, 20, 62, 160),
    )
    out = vi.indicator("out", I32, bounds=(40, 200, 62, 340))
    vi.wire(n, out)
    project.add(vi)
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    written = (package.root / f"{STEM}.py").read_text(encoding="utf-8")
    assert "states={'n': {'visible': False, 'disabled': 2}}" in written
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        await user.should_not_see(kind=ui.number)
        (number,) = [
            e for e in user.client.elements.values() if isinstance(e, ui.number)
        ]
        assert not _box_visible(number)
        assert not number.enabled
        assert "opacity-40" in number.classes

    simulate(panel.build_panel, scenario)


def test_a_control_stored_in_the_default_state_has_no_entry(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    out = vi.indicator("out", I32)
    vi.wire(n, out)
    project.add(vi)
    package = project.generate(NAME, panel=True)
    written = (package.root / f"{STEM}.py").read_text(encoding="utf-8")
    assert "Panel(" in written
    assert "states=" not in written


def test_the_user_moving_the_focus_is_the_panels_key_focus(
    project: Project,  # noqa: F811
) -> None:
    project.add(_property_vi())
    package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)
    vi_panel = panel.vi_function.panel

    async def scenario(user: User) -> None:
        (number,) = found(user, ui.number)
        assert vi_panel.property_of("n", "key_focus", False) is False
        fire(number, "focus", {})
        assert vi_panel.property_of("n", "key_focus") is True
        fire(number, "blur", {})
        assert vi_panel.property_of("n", "key_focus") is False

    simulate(panel.build_panel, scenario)


def _read_then_write_vi() -> Vi:
    """The control ``n`` (stored hidden) has a Visible row read into ``seen``
    first and a Visible row written True after."""
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED, ddo_flags=HIDDEN)
    seen = vi.indicator("seen", BOOL, ddo_class="stdBool")
    read, write = value_property(vi, n, ["Visible", "Visible"])
    vi.wire(vi.bool_constant(True), write)
    vi.wire(read, seen)
    return vi


def test_get_property_gives_the_panels_state_and_the_held_value_with_no_panel(
    project: Project,  # noqa: F811
) -> None:
    project.add(_read_then_write_vi())
    package = project.generate(NAME)
    assert package.gaps == []
    assert "live_io.get_property(PanelDemoValue.N, PanelDemoProperty.VISIBLE" in (
        package.source
    )
    # No panel: the function's own variable answers, which is the stored state.
    assert package.function(n=1).seen is False
    # The write left the control visible, from one call to the next.
    assert package.function(n=1).seen is True


def test_get_property_with_a_panel_reads_the_panel_not_the_held_value(
    project: Project,  # noqa: F811
) -> None:
    project.add(_read_then_write_vi())
    package = project.generate(NAME)
    panel = package.function.panel
    panel.run()
    assert panel.get("seen") is False  # the stored state, held by the panel
    # The first run's write left the function's variable True; a view then
    # noted that the control is hidden: the panel's state is what the VI reads.
    panel.note_property("n", "visible", False)
    panel.run()
    assert panel.get("seen") is False
    panel.note_property("n", "visible", True)
    panel.run()
    assert panel.get("seen") is True


def test_the_base_get_property_returns_the_value_it_is_given() -> None:
    assert LiveIO().get_property("n", "visible", 7) == 7


def test_a_property_a_widget_cannot_show_is_a_warning_and_raises_where_set(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    flag = vi.control("flag", BOOL, REQUIRED, ddo_class="stdBool")
    out = vi.indicator("out", BOOL, ddo_class="stdBool")
    (maximum,) = value_property(vi, flag, ["Maximum"])
    vi.wire(vi.int_constant(3), maximum)
    vi.wire(flag, out)
    project.add(vi)
    package = project.generate(NAME, panel=True)
    gaps = [g for g in package.gaps if g.kind == UNSUPPORTED_CONTROL_PROPERTY]
    assert [(g.node_name, g.identifier) for g in gaps] == [("flag", "maximum")]
    assert "cannot show its 'maximum' property" in gaps[0].reason
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        with pytest.raises(NotImplementedError, match="'maximum' of 'flag'"):
            panel.vi_function.panel._live_property("flag", "maximum", 3)

    simulate(panel.build_panel, scenario)


def test_a_greyed_out_boolean_indicator_starts_greyed_with_no_warning(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    flag = vi.indicator(
        "lamp", BOOL, ddo_class="stdBool", ddo_flags=DISABLED | GREYED_OUT
    )
    vi.wire(vi.bool_constant(True), flag)
    project.add(vi)
    package = project.generate(NAME, panel=True)
    assert [g for g in package.gaps if g.kind == UNSUPPORTED_CONTROL_PROPERTY] == []
    panel = panel_of(package, STEM)

    async def scenario(user: User) -> None:
        (light,) = found(user, ui.icon)[-1:]
        assert "opacity-40" in light.classes
        assert "pointer-events-none" in light.classes

    simulate(panel.build_panel, scenario)


def test_disabled_on_a_cluster_disables_every_input_inside_it() -> None:
    members = [
        controls.Member("a", "a", controls.number_port, 0.0, False),
        controls.Member("b", "b", controls.string_port, ""),
    ]
    ports: list[Any] = []

    def build() -> None:
        ports.append(controls.cluster_port(False, members, readonly=False))

    async def scenario(user: User) -> None:
        inputs = found(user, ui.number) + found(user, ui.input)
        ports[0].properties["disabled"](2)
        assert [e.enabled for e in inputs] == [False, False]
        ports[0].properties["disabled"](0)
        assert [e.enabled for e in inputs] == [True, True]

    simulate(build, scenario)


def _port_makers() -> dict[PanelKind, Callable[[bool], Any]]:
    """A builder of the port of each kind of widget, by the kind, taking
    ``readonly``."""
    number = controls.Member("x", "x", controls.number_port, 0.0, False)
    return {
        PanelKind.NUMBER: lambda ro: controls.number_port(0.0, False, readonly=ro),
        PanelKind.COMPLEX: lambda ro: controls.complex_port(0j, readonly=ro),
        PanelKind.BOOLEAN: lambda ro: controls.bool_port(False, readonly=ro),
        PanelKind.STRING: lambda ro: controls.string_port("", readonly=ro),
        PanelKind.PATH: lambda ro: controls.path_port("", readonly=ro),
        PanelKind.ENUM: lambda ro: controls.choice_port((("a", 0),), 0, readonly=ro),
        PanelKind.VARIANT: lambda ro: controls.variant_port(readonly=ro),
        PanelKind.ARRAY: lambda ro: controls.array_port(number, 3, [], readonly=ro),
        PanelKind.CLUSTER: lambda ro: controls.cluster_port(
            False, [number], readonly=ro
        ),
        PanelKind.ERROR: lambda ro: controls.error_port(readonly=ro),
    }


@pytest.mark.parametrize("is_indicator", [False, True])
def test_the_generators_table_matches_what_each_widget_can_show(
    is_indicator: bool,
) -> None:
    shown: dict[PanelKind, set[str]] = {}

    def build() -> None:
        for kind, make in _port_makers().items():
            shown[kind] = set(make(is_indicator).properties)

    async def scenario(user: User) -> None:
        for kind, properties in shown.items():
            expected = SHOWN_PROPERTIES[(kind, is_indicator)] - {"visible"}
            assert properties == expected, kind

    simulate(build, scenario)
