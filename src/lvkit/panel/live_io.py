"""The values a function exchanges, while it runs, with whatever runs it.

A function converted from a VI that reads or writes its LabVIEW front panel
while it runs takes ``live_io``: it calls ``write`` after it writes an
indicator, ``read`` before it uses a control it reads while running,
``set_property`` when it sets a control property, ``prompt`` when it shows a
dialog box, calls ``open_panel`` / ``close_panel`` when it opens or closes its
own front panel window, calls ``set_window_title`` when it writes that window's
title, and takes the front panel's events from ``events``.
The base class
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

    def open_panel(self, activate: bool = False, state: int = 1) -> None:
        """The function opened its own front panel window (Front Panel:Open,
        FP.Open).
        ``activate`` is NI's Activate?: False (default), the window does not open
        as the active window; True, it does. ``state`` is NI's State, the state to
        open the window in: 1 Standard (default), 3 Hidden, 4 Minimized, 5
        Maximized (0 Invalid and 2 Closed are errors in LabVIEW). Called again
        while the window is open, it sets the window's state to ``state``
        (docs-be ``properties-and-methods/vi-server/vi/openfrontpanel.html``)."""
        return None

    def close_panel(self) -> None:
        """The function closed its own front panel window (Front Panel:Close,
        FP.Close, which has no inputs: docs-be
        ``properties-and-methods/vi-server/vi/closefrontpanel.html``)."""
        return None

    def set_window_title(self, title: str) -> None:
        """The function wrote the Title of its own front panel window (Front Panel
        Window:Title, FP.Title: docs-be
        ``properties-and-methods/vi-server/vi/fpwintitle.html``): the string in
        the window's title bar. The function keeps the title for its own later
        reads."""
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
