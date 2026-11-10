"""The value port of one widget, and how a port is tied to the holder.

Every kind of control is built as a ``Port``: ``get`` reads the value the
function takes, ``show`` puts a value in the widget, and the listeners are told
when the user changes it. A control or indicator of the panel ties its port to
the ``LiveValues`` holder by name; a field of a cluster or a column of an array
is built from a ``Member`` and tied to the port of the cluster or array that
holds it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .live import LiveValues


class Port:
    def __init__(self, get: Callable[[], Any], show: Callable[[Any], None]) -> None:
        self.get = get
        self.show = show
        self._listeners: list[Callable[[Any], None]] = []

    def on_change(self, listener: Callable[[Any], None]) -> None:
        self._listeners.append(listener)

    def changed(self) -> None:
        """The user changed the widget: tell the listeners the new value."""
        value = self.get()
        for listener in self._listeners:
            listener(value)


class Member:
    """A field of a cluster or a column of an array: its ``key`` (the field's
    attribute name, or its position), its ``label`` and the port builder that
    draws it, with the arguments the builder takes before ``readonly``."""

    def __init__(
        self, key: str, label: str, builder: Callable[..., Port], *arguments: Any
    ) -> None:
        self.key = key
        self.label = label
        self.builder = builder
        self.arguments = arguments

    def build(self, *, readonly: bool) -> Port:
        return self.builder(*self.arguments, readonly=readonly)


def bind_control(live: LiveValues, name: Any, port: Port) -> Port:
    """A control: the holder starts with the port's value and follows its
    changes."""
    live.add_control(name, port.get())
    port.on_change(lambda value: live.set_control(name, value))
    return port


def bind_indicator(live: LiveValues, name: Any, port: Port) -> Port:
    """An indicator: the holder shows its values in the port."""
    live.add_indicator(name, port.show)
    return port
