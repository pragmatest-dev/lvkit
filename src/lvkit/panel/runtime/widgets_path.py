"""The widget of a path control and indicator.

The value that crosses to the function is a ``pathlib.Path`` (the empty path is
``Path("")``, the default of a path); the widget shows its text. The text is
turned into a ``Path`` here and nowhere else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import ui

from .filepicker import FilePicker
from .port import Port, bind_control, bind_indicator
from .view import PanelWidgets


def path_text(value: Any) -> str:
    """The text of a path value; none (or the empty path) shows no text."""
    if value is None or value == Path(""):
        return ""
    return str(value)


def path_port(default: str, *, readonly: bool) -> Port:
    port: Port
    widget = ui.input(
        value=default,
        on_change=None if readonly else lambda e: port.changed(),
    ).props("outlined dense readonly" if readonly else "outlined dense")
    widget.classes("w-full")
    port = Port(
        lambda: Path(widget.value or ""),
        lambda value: widget.set_value(path_text(value)),
    )
    if not readonly:

        async def browse() -> None:
            picked = await FilePicker(widget.value or str(Path.home()))
            if picked:
                widget.set_value(picked)

        with widget.add_slot("append"):
            button = ui.button(icon="folder_open", on_click=browse)
            button.props("flat dense round size=sm").classes("text-gray-500")
            button.mark("browse")
            ui.tooltip("Browse")
    return port


def path_control(view: PanelWidgets, name: Any, default: str) -> Port:
    return bind_control(view, name, path_port(default, readonly=False))


def path_indicator(view: PanelWidgets, name: Any, default: str) -> Port:
    return bind_indicator(view, name, path_port(default, readonly=True))
