"""The widget of an enum or ring control and indicator: a select of the items.

The value that crosses to the function is the item's value, a plain ``int`` (the
item's number for an enum, the value the ring stores for it for a ring).
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from .port import Port, bind_control, bind_indicator
from .properties import widget_properties
from .view import PanelWidgets


def choice_port(
    items: tuple[tuple[str, int], ...], default: int, *, readonly: bool
) -> Port:
    options = {value: label for label, value in items}
    port: Port
    widget = ui.select(
        options,
        value=default,
        on_change=None if readonly else lambda e: port.changed(),
    ).props("outlined dense readonly" if readonly else "outlined dense")
    widget.classes("w-full")

    def show(value: Any) -> None:
        number = int(value)
        if number not in widget.options:
            widget.options[number] = str(number)
            widget.update()
        widget.set_value(number)

    port = Port(
        lambda: widget.value,
        show,
        widget_properties(widget, focusable=None if readonly else widget),
        None if readonly else widget,
    )
    return port


def choice_control(
    view: PanelWidgets, name: Any, items: tuple[tuple[str, int], ...], default: int
) -> Port:
    return bind_control(view, name, choice_port(items, default, readonly=False))


def choice_indicator(
    view: PanelWidgets, name: Any, items: tuple[tuple[str, int], ...], default: int
) -> Port:
    return bind_indicator(view, name, choice_port(items, default, readonly=True))


def combo_port(default: str, *, readonly: bool) -> Port:
    """A Combo Box: a select of its strings that also takes text the user types.
    The value that crosses to the function is the text; ``Strings[]`` replaces the
    list of the drop-down (``properties.STRINGS``), and the text it holds stays
    selectable, as in LabVIEW where the value need not be one of the strings."""
    held = str(default)
    strings: list[str] = []
    port: Port

    def options() -> list[str]:
        return [*strings] if not held or held in strings else [*strings, held]

    def changed(event: Any) -> None:
        nonlocal held
        held = str(event.value or "")
        port.changed()

    widget = ui.select(
        options(),
        value=held or None,
        with_input=True,
        new_value_mode=None if readonly else "add-unique",
        on_change=None if readonly else changed,
    ).props("outlined dense readonly" if readonly else "outlined dense")
    widget.classes("w-full")

    def show(value: Any) -> None:
        nonlocal held
        held = str(value)
        widget.set_options(options(), value=held or None)

    def show_strings(value: Any) -> None:
        nonlocal strings
        strings = [str(item) for item in value]
        widget.set_options(options())

    port = Port(
        lambda: held,
        show,
        widget_properties(
            widget, focusable=None if readonly else widget, strings=show_strings
        ),
        None if readonly else widget,
    )
    return port


def combo_control(view: PanelWidgets, name: Any, default: str) -> Port:
    return bind_control(view, name, combo_port(default, readonly=False))


def combo_indicator(view: PanelWidgets, name: Any, default: str) -> Port:
    return bind_indicator(view, name, combo_port(default, readonly=True))
