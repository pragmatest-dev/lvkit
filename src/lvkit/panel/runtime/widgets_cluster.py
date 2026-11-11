"""The widget of a cluster control and indicator: a bordered box with each field
built by its own kind. A field the panel does not draw (a reference or LabVIEW
class object) has no caption and holds ``None``.

The value that crosses to the function is the one it holds a cluster as: an
object with an attribute for each field (``types.SimpleNamespace``) for a cluster
type definition, and a tuple of the fields in order for any other cluster.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from nicegui import ui

from .port import Member, Port, bind_control, bind_indicator
from .view import PanelWidgets
from .widgets import caption
from .widgets_opaque import hidden_port


def _field(value: Any, member: Member, position: int, named: bool) -> Any:
    return getattr(value, member.key) if named else value[position]


def cluster_port(named: bool, members: list[Member], *, readonly: bool) -> Port:
    ports: list[Port] = []
    with ui.column().classes("w-full gap-1 p-2 border rounded"):
        for member in members:
            if member.builder is not hidden_port:
                caption(member.label)
            ports.append(member.build(readonly=readonly))

    def get() -> Any:
        values = [port.get() for port in ports]
        if named:
            return SimpleNamespace(
                **{m.key: v for m, v in zip(members, values, strict=True)}
            )
        return tuple(values)

    def show(value: Any) -> None:
        if value is None:
            return
        for position, (member, port) in enumerate(zip(members, ports, strict=True)):
            port.show(_field(value, member, position, named))

    cluster = Port(get, show)
    for port in ports:
        port.on_change(lambda _: cluster.changed())
    return cluster


def cluster_control(
    view: PanelWidgets, name: Any, named: bool, members: list[Member]
) -> Port:
    return bind_control(view, name, cluster_port(named, members, readonly=False))


def cluster_indicator(
    view: PanelWidgets, name: Any, named: bool, members: list[Member]
) -> Port:
    return bind_indicator(view, name, cluster_port(named, members, readonly=True))
