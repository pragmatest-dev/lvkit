"""The widgets of a front panel for a numeric, a Boolean and a string, each built as
a ``Port`` (``port.py``) and as a control (the user changes it, the function reads
it) or an indicator (the function writes it, the panel shows it).

A control keeps the ``LiveValues`` holder up to date from its change handler. An
indicator registers the function that shows a value in it; the holder calls that
function on the event loop.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from .live import LiveValues
from .port import Port, bind_control, bind_indicator

CAPTION_CLS = "text-xs font-semibold text-gray-500 truncate leading-none"


def caption(text: str) -> None:
    """The label of a control, above it."""
    ui.label(text).classes(CAPTION_CLS)


def as_number(value: Any, integer: bool) -> Any:
    if value is None:
        return None
    return int(round(float(value))) if integer else float(value)


def number_port(default: float, integer: bool, *, readonly: bool) -> Port:
    if readonly:
        shown = ui.input(value=str(as_number(default, integer)))
        shown.props("outlined dense readonly").classes("w-full")
        current = as_number(default, integer)

        def show_number(value: Any) -> None:
            nonlocal current
            current = value
            shown.set_value(str(value))

        return Port(lambda: current, show_number)
    held = as_number(default, integer)
    port: Port

    def changed(event: Any) -> None:
        nonlocal held
        if event.value is not None:
            held = as_number(event.value, integer)
            port.changed()

    widget = ui.number(value=default, on_change=changed).props("outlined dense")
    if integer:
        widget.props("step=1")
        widget.format = "%d"
    widget.classes("w-full")
    port = Port(lambda: held, lambda value: widget.set_value(value))
    return port


def bool_port(default: bool, *, readonly: bool) -> Port:
    if readonly:
        light = ui.icon("circle", size="28px")
        lit = bool(default)

        def show_light(value: Any) -> None:
            nonlocal lit
            lit = bool(value)
            light.classes(replace="text-green-500" if lit else "text-gray-400")

        show_light(default)
        return Port(lambda: lit, show_light)
    port: Port
    switch = ui.switch(value=bool(default), on_change=lambda e: port.changed())
    port = Port(lambda: bool(switch.value), lambda value: switch.set_value(bool(value)))
    return port


def string_port(default: str, *, readonly: bool) -> Port:
    port: Port
    widget = ui.input(
        value=str(default),
        on_change=None if readonly else lambda e: port.changed(),
    ).props("outlined dense readonly" if readonly else "outlined dense")
    widget.classes("w-full")
    port = Port(
        lambda: str(widget.value or ""), lambda value: widget.set_value(str(value))
    )
    return port


def number_control(live: LiveValues, name: Any, default: float, integer: bool) -> Port:
    return bind_control(live, name, number_port(default, integer, readonly=False))


def number_indicator(
    live: LiveValues, name: Any, default: float, integer: bool
) -> Port:
    return bind_indicator(live, name, number_port(default, integer, readonly=True))


def bool_control(live: LiveValues, name: Any, default: bool) -> Port:
    return bind_control(live, name, bool_port(default, readonly=False))


def bool_indicator(live: LiveValues, name: Any, default: bool) -> Port:
    return bind_indicator(live, name, bool_port(default, readonly=True))


def string_control(live: LiveValues, name: Any, default: str) -> Port:
    return bind_control(live, name, string_port(default, readonly=False))


def string_indicator(live: LiveValues, name: Any, default: str) -> Port:
    return bind_indicator(live, name, string_port(default, readonly=True))
