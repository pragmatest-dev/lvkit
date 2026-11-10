"""The values a function exchanges, while it runs, with whatever runs it.

A function converted from a VI that reads or writes its LabVIEW front panel
while it runs takes ``live_io``: it calls ``write`` after it writes an
indicator, ``read`` before it uses a control it reads while running,
``set_property`` when it sets a control property, and takes the front panel's
events from ``events``. The base class does nothing: ``read`` returns the value
the function already holds. Pass a subclass to show, record or supply the
values (a UI, a test, a data service).
"""

from __future__ import annotations

from enum import Enum
from queue import Queue
from typing import Any


class LiveIO:
    """The base class: every method does nothing, so a function called without
    a ``live_io`` runs as if its front panel were not there."""

    events: Queue[Any] | None = None
    """The front panel's events, for a function that holds an Event Structure.
    None: the function waits on an empty queue of its own."""

    def write(self, name: Enum, value: Any) -> None:
        """The function wrote the indicator ``name`` (or the control ``name``
        through a Local Variable): ``value`` is what it now holds."""
        return None

    def read(self, name: Enum, value: Any) -> Any:
        """The value of the control ``name`` the function uses now. ``value`` is
        the one the function holds; the base class returns it."""
        return value

    def set_property(self, name: Enum, prop: Enum, value: Any) -> Any:
        """The function set the property ``prop`` of the control or indicator
        ``name`` to ``value``."""
        return None


NO_LIVE_IO = LiveIO()
