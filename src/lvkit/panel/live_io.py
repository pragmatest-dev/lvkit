"""The values a function exchanges, while it runs, with whatever runs it.

A function converted from a VI that reads or writes its LabVIEW front panel
while it runs takes ``live_io``: it calls ``write`` after it writes an
indicator, ``read`` before it uses a control it reads while running,
``set_property`` when it sets a control property, ``prompt`` when it shows a
dialog box, and takes the front panel's events from ``events``. The base class
does nothing, except ``prompt``, which asks on the terminal: ``read`` returns
the value the function already holds. Pass a subclass to show, record or supply
the values (a UI, a test, a data service).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from enum import Enum
from queue import Queue
from typing import Any


class PromptUnavailableError(RuntimeError):
    """A dialog box has no one to answer it: the function was called without a
    ``live_io`` that shows dialogs, and standard input is not a terminal."""


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

    def prompt(
        self, message: str, buttons: Sequence[str] = ("OK",), title: str = ""
    ) -> int:
        """The function shows a dialog box with ``message`` and a button for
        each of ``buttons`` (left to right; a button whose text is empty is not
        shown, and a dialog with none shown has one ``OK`` button). Returns the
        position in ``buttons`` of the button the user chose.

        The base class asks on the terminal: it prints the message, then reads
        the number of the button (or Enter, for a dialog with one button). With
        no terminal (standard input is not a tty) it raises
        ``PromptUnavailableError``, which names the dialog."""
        shown = [(i, text) for i, text in enumerate(buttons) if text] or [(0, "OK")]
        if not sys.stdin.isatty():
            raise PromptUnavailableError(
                f"Cannot show the dialog {title or message!r}: no live_io that "
                "shows dialogs was passed, and standard input is not a terminal."
            )
        if title:
            print(f"\n[{title}]")
        print(message)
        if len(shown) == 1:
            input(f"[{shown[0][1]}] press Enter to continue: ")
            return shown[0][0]
        for number, (_, text) in enumerate(shown, 1):
            print(f"  {number}. {text}")
        while True:
            try:
                chosen = int(input("Select a button: "))
            except ValueError:
                chosen = 0
            if 1 <= chosen <= len(shown):
                return shown[chosen - 1][0]
            print("Not a button, try again.")


NO_LIVE_IO = LiveIO()
