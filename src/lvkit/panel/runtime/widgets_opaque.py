"""The widgets of a front panel for a variant, which shows its value as text, and
for a reference or LabVIEW class object, which the panel does not draw.

A variant control and a field of a cluster hold ``None`` and an indicator shows the
text of the value the function writes. A reference or class object has no widget:
its port draws nothing and gives ``None``, the default of its type, so a cluster
that holds one still has a value for that field.
"""

from __future__ import annotations

from typing import Any

from nicegui import ui

from .port import Port, bind_control, bind_indicator
from .properties import widget_properties
from .view import PanelWidgets


def variant_port(*, readonly: bool) -> Port:
    held: Any = None
    shown = ui.input(value=repr(held)).props("outlined dense readonly")
    shown.classes("w-full")

    def show_variant(value: Any) -> None:
        nonlocal held
        held = value
        shown.set_value(repr(getattr(value, "value", value)))

    return Port(lambda: held, show_variant, widget_properties(shown))


def hidden_port(*, readonly: bool) -> Port:
    return Port(lambda: None, lambda value: None)


def unsupported_control(reason: str) -> None:
    """The place of a control the panel has no widget for: its caption (drawn by the
    caller) and the reason it is not converted. It is tied to no name, so the
    panel keeps the control's own default for a run and an indicator's value is
    kept without being drawn."""
    ui.label(f"This control is not converted: {reason}").classes(
        "text-xs text-gray-500 border border-dashed rounded px-2 py-1"
    )


def variant_control(view: PanelWidgets, name: Any) -> Port:
    return bind_control(view, name, variant_port(readonly=False))


def variant_indicator(view: PanelWidgets, name: Any) -> Port:
    return bind_indicator(view, name, variant_port(readonly=True))
