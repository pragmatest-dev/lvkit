"""A Combo Box on the generated panel: the NiceGUI page draws it as a select that
takes typed text, ``Strings[]`` written by a Property Node (bound to the control, or
by a control reference through the panel) replaces its drop-down list, and a
Property Node bound to the control reads the last write, else the strings the file
saves it with. Each case is a tiny diagram (``tests/panel_diagrams.py``) generated,
imported and driven headless with NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from nicegui import ui
from nicegui.testing.user import User

from lvkit.codegen.gaps import UNSUPPORTED_CONTROL_PROPERTY
from lvkit.panel.kinds import SHOWN_PROPERTIES, PanelKind
from tests.error_diagrams import (
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.panel_diagrams import array_element, array_type, value_property
from tests.panel_harness import found, panel_of, simulate

NAME = "Combo Demo.vi"
STEM = "combo_demo"
STRINGS = ("Strings []", 104099843)


def _combo_vi(*, write: bool) -> Vi:
    """A Combo Box ``c``, the array control ``items`` and the array indicator
    ``listing``: the VI writes ``items`` to the Strings[] of ``c`` (``write``) and
    reads Strings[] of ``c`` into ``listing``."""
    vi = Vi(NAME)
    combo = vi.control(
        "c", STR, REQUIRED, ddo_class="stdComboBox", bounds=(40, 20, 62, 160)
    )
    strings = array_type(vi, STR)
    items = vi.control(
        "items",
        strings,
        REQUIRED,
        ddo_class="indArr",
        inner=array_element(vi, "stdString", STR),
        bounds=(110, 20, 200, 160),
    )
    listing = vi.indicator(
        "listing",
        strings,
        ddo_class="indArr",
        inner=array_element(vi, "stdString", STR),
        bounds=(110, 220, 200, 360),
    )
    rows = [STRINGS, STRINGS] if write else [STRINGS]
    terminals = value_property(vi, combo, rows, class_name="CmbBox")
    if write:
        vi.wire(items, terminals[0])
    vi.wire(terminals[-1], listing)
    return vi


def test_the_page_draws_a_combo_box_as_a_select_that_takes_typed_text(
    project: Project,  # noqa: F811
) -> None:
    project.add(_combo_vi(write=False))
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, STEM)
    vi_panel = panel.vi_function.panel

    async def scenario(user: User) -> None:
        (select,) = found(user, ui.select)
        assert select.options == []
        assert select._props["new-value-mode"] == "add-unique"  # noqa: SLF001
        vi_panel.put("c", "typed")
        await asyncio.sleep(0.4)
        # the text of the control is kept selectable though it is not a string of
        # the list
        assert select.value == "typed"
        assert select.options == ["typed"]

    simulate(panel.build_panel, scenario)


def test_a_write_to_strings_shows_the_new_list_and_keeps_the_text(
    project: Project,  # noqa: F811
) -> None:
    project.add(_combo_vi(write=True))
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    written = (package.root / f"{STEM}.py").read_text(encoding="utf-8")
    assert "states={'c': {'strings': []}}" in written
    panel = panel_of(package, STEM)
    vi_panel = panel.vi_function.panel

    async def scenario(user: User) -> None:
        (select,) = found(user, ui.select)
        vi_panel.put("c", "b")
        vi_panel.put("items", ["a", "b", "c"])
        await asyncio.sleep(0.4)
        vi_panel.run()
        await asyncio.sleep(0.4)
        assert select.options == ["a", "b", "c"]
        assert select.value == "b"
        assert vi_panel.property_of("c", "strings") == ["a", "b", "c"]
        # a later write replaces the list; the text the control holds stays
        vi_panel.put("items", ["x"])
        vi_panel.run()
        await asyncio.sleep(0.4)
        assert select.options == ["x", "b"]
        assert select.value == "b"
        # and the VI read back what it wrote
        assert vi_panel.get("listing") == ["x"]

    simulate(panel.build_panel, scenario)


def test_a_read_gives_the_saved_strings_then_the_last_write(
    project: Project,  # noqa: F811
) -> None:
    project.add(_combo_vi(write=True))
    package = project.generate(NAME, panel=True)
    vi_panel = panel_of(package, STEM).vi_function.panel
    function = vi_panel.function
    # a bound read after the write in the same call is the value just written
    assert function(c="", items=["p", "q"]).listing == ["p", "q"]
    assert function(c="", items=[]).listing == []


def test_a_combo_box_reads_its_saved_strings_before_any_write(
    project: Project,  # noqa: F811
) -> None:
    project.add(_combo_vi(write=False))
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    function = panel_of(package, STEM).vi_function.panel.function
    assert function(c="", items=["ignored"]).listing == []


def test_a_combo_box_saved_with_strings_is_warned_about_not_read_silently(
    project: Project,  # noqa: F811
) -> None:
    """No corpus VI is saved with a drop-down list, so the form of an item is not
    read: parsing says so instead of starting the list empty without a word."""
    vi = Vi(NAME)
    combo = vi.control(
        "c",
        STR,
        REQUIRED,
        ddo_class="stdComboBox",
        inner='<comboBoxValues elements="2" />',
    )
    out = vi.indicator("out", STR, ddo_class="stdString")
    vi.wire(combo, out)
    project.add(vi)
    with pytest.warns(UserWarning, match="Combo Box 'c' is saved with strings"):
        project.generate(NAME, panel=True)


def test_strings_is_shown_by_a_combo_box_and_by_no_other_kind() -> None:
    shown = {
        kind for (kind, _), props in SHOWN_PROPERTIES.items() if "strings" in props
    }
    assert shown == {PanelKind.COMBO}


def test_strings_of_a_string_control_is_a_panel_generator_gap(
    project: Project,  # noqa: F811
) -> None:
    """The neighbouring control: a string control has no drop-down list, so the
    panel warns that it cannot show the property."""
    vi = Vi(NAME)
    text = vi.control("t", STR, REQUIRED, ddo_class="stdString")
    vi.indicator("out", STR, ddo_class="stdString")
    value_property(vi, text, [STRINGS], class_name="CmbBox")
    project.add(vi)
    package = project.generate(NAME, panel=True)
    kinds: list[Any] = [gap.kind for gap in package.gaps]
    assert UNSUPPORTED_CONTROL_PROPERTY in kinds
