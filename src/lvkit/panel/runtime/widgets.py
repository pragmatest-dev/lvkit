"""The widgets of a front panel: a numeric, a Boolean and a string, each as a
control (the user changes it, the function reads it) and as an indicator (the
function writes it, the panel shows it).

A control keeps the ``LiveValues`` holder up to date from its change handler. An
indicator registers the function that shows a value in it; the holder calls that
function on the event loop.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from .live import LiveValues

CAPTION_CLS = "text-xs font-semibold text-gray-500 truncate leading-none"


def caption(text: str) -> None:
    """The label of a control, above it."""
    ui.label(text).classes(CAPTION_CLS)


def _number(value: Any, integer: bool) -> Any:
    if value is None:
        return None
    return int(round(float(value))) if integer else float(value)


def number_control(
    live: LiveValues, name: Any, default: float, integer: bool
) -> ui.number:
    live.add_control(name, _number(default, integer))

    def changed(event: Any) -> None:
        if event.value is not None:
            live.set_control(name, _number(event.value, integer))

    widget = ui.number(value=default, on_change=changed).props("outlined dense")
    if integer:
        widget.props("step=1")
        widget.format = "%d"
    return widget.classes("w-full")


def number_indicator(
    live: LiveValues, name: Any, default: float, integer: bool
) -> ui.input:
    widget = (
        ui.input(value=str(_number(default, integer)))
        .props("outlined dense readonly")
        .classes("w-full")
    )
    live.add_indicator(name, lambda value: widget.set_value(str(value)))
    return widget


def bool_control(live: LiveValues, name: Any, default: bool) -> ui.switch:
    live.add_control(name, bool(default))
    return ui.switch(
        value=bool(default), on_change=lambda e: live.set_control(name, bool(e.value))
    )


def bool_indicator(live: LiveValues, name: Any, default: bool) -> ui.icon:
    widget = ui.icon("circle", size="28px")

    def show(value: Any) -> None:
        widget.classes(
            replace="text-green-500" if value else "text-gray-400",
        )

    show(default)
    live.add_indicator(name, show)
    return widget


def string_control(live: LiveValues, name: Any, default: str) -> ui.input:
    live.add_control(name, str(default))
    return (
        ui.input(
            value=str(default),
            on_change=lambda e: live.set_control(name, str(e.value or "")),
        )
        .props("outlined dense")
        .classes("w-full")
    )


def string_indicator(live: LiveValues, name: Any, default: str) -> ui.input:
    widget = (
        ui.input(value=str(default)).props("outlined dense readonly").classes("w-full")
    )
    live.add_indicator(name, lambda value: widget.set_value(str(value)))
    return widget
