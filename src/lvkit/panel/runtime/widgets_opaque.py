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
from .view import PanelWidgets


def variant_port(*, readonly: bool) -> Port:
    held: Any = None
    shown = ui.input(value=repr(held)).props("outlined dense readonly")
    shown.classes("w-full")

    def show_variant(value: Any) -> None:
        nonlocal held
        held = value
        shown.set_value(repr(getattr(value, "value", value)))

    return Port(lambda: held, show_variant)


def hidden_port(*, readonly: bool) -> Port:
    return Port(lambda: None, lambda value: None)


def variant_control(view: PanelWidgets, name: Any) -> Port:
    return bind_control(view, name, variant_port(readonly=False))


def variant_indicator(view: PanelWidgets, name: Any) -> Port:
    return bind_indicator(view, name, variant_port(readonly=True))
