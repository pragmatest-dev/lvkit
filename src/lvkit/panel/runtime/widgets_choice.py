"""The widget of an enum or ring control and indicator: a select of the items.

The value that crosses to the function is the item's value, a plain ``int`` (the
item's number for an enum, the value the ring stores for it for a ring).
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from .port import Port, bind_control, bind_indicator
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

    port = Port(lambda: widget.value, show)
    return port


def choice_control(
    view: PanelWidgets, name: Any, items: tuple[tuple[str, int], ...], default: int
) -> Port:
    return bind_control(view, name, choice_port(items, default, readonly=False))


def choice_indicator(
    view: PanelWidgets, name: Any, items: tuple[tuple[str, int], ...], default: int
) -> Port:
    return bind_indicator(view, name, choice_port(items, default, readonly=True))
