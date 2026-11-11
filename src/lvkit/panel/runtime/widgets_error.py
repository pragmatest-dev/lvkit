"""The widget of an error cluster control and indicator: a bordered box with a
status light, a code number and a source text, as LabVIEW lays out an error
cluster.

The value that crosses to the function is the one it holds an error wire as:
``None`` for no error, else the package's ``LabVIEWError``. Status off with code
0 and no source is no error; anything else is a ``LabVIEWError`` (status off
with a code is a warning). An indicator shows a ``LabVIEWError``'s message
under the source when it has one.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from .port import Port, bind_control, bind_indicator
from .view import PanelWidgets
from .widgets import CAPTION_CLS, caption

LIGHT_ON = "text-red-500"
LIGHT_OFF = "text-green-500"


def as_error(status: bool, code: Any, source: str) -> Any:
    """``None`` for no error, else the package's ``LabVIEWError``. The class is
    imported here, when an error is built, because this package imports only
    inside a generated package, where ``..errors`` is the package's own module."""
    from ..errors import LabVIEWError  # type: ignore[import-not-found]

    number = int(round(float(code or 0)))
    if not status and number == 0 and not source:
        return None
    return LabVIEWError(code=number, source=source, status=status)


def error_port(*, readonly: bool) -> Port:
    port: Port
    status = False
    with ui.column().classes("w-full gap-1 p-2 border rounded"):
        caption("status")
        if readonly:
            light = ui.icon("circle", size="28px").classes(LIGHT_OFF)
            switch = None
        else:
            switch = ui.switch(value=False, on_change=lambda e: port.changed())
        caption("code")
        code = ui.number(
            value=0,
            on_change=None if readonly else lambda e: port.changed(),
        ).props("outlined dense readonly step=1" if readonly else "outlined dense")
        code.format = "%d"
        code.classes("w-full")
        caption("source")
        source = ui.input(
            value="",
            on_change=None if readonly else lambda e: port.changed(),
        ).props("outlined dense readonly" if readonly else "outlined dense")
        source.classes("w-full")
        message = ui.label("").classes(CAPTION_CLS)
        message.set_visibility(False)

    def get() -> Any:
        if switch is None:
            return as_error(status, code.value, str(source.value or ""))
        return as_error(bool(switch.value), code.value, str(source.value or ""))

    def show(value: Any) -> None:
        nonlocal status
        if value is None:
            status, number, text, note = False, 0, "", ""
        else:
            status = bool(value.status)
            number, text, note = value.code, value.source, value.message
        if switch is None:
            light.classes(replace=LIGHT_ON if status else LIGHT_OFF)
        else:
            switch.set_value(status)
        code.set_value(number)
        source.set_value(text)
        message.set_text(note)
        message.set_visibility(bool(note))

    port = Port(get, show)
    return port


def error_control(view: PanelWidgets, name: Any) -> Port:
    return bind_control(view, name, error_port(readonly=False))


def error_indicator(view: PanelWidgets, name: Any) -> Port:
    return bind_indicator(view, name, error_port(readonly=True))
