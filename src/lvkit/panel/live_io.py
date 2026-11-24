"""The values a function exchanges, while it runs, with whatever runs it.

A function converted from a VI that reads or writes its LabVIEW front panel
while it runs takes ``live_io``: it calls ``write`` after it writes an
indicator, ``read`` before it uses a control it reads while running,
``set_property`` when it sets a control property, ``get_property`` when it reads
one, ``prompt`` when it shows a
dialog box, calls ``open_panel`` / ``close_panel`` when it opens or closes its
own front panel window, calls ``center_panel`` when it centers it,
``set_window_title`` / ``set_window_bounds`` when it writes that window's title or
bounds, ``set_window_state`` / ``set_panel_bounds`` / ``set_panel_origin`` /
``set_window_monitor`` / ``set_window_behavior`` when it writes the window's other
properties, ``bring_to_front`` / ``is_frontmost`` to bring it to the front or ask
whether it is, ``monitors`` for the computer's screens, and takes the front panel's
events from ``events``.
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

    def get_property(self, name: Enum, prop: Enum, value: Any) -> Any:
        """The property ``prop`` of the control or indicator ``name`` that the
        function reads now. ``value`` is the one the function holds (the state
        the control started in, or the last value the function set); the base
        class returns it."""
        return value

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

    def set_window_bounds(self, bounds: tuple[int, int, int, int]) -> None:
        """The function wrote the Window Bounds of its own front panel window
        (Front Panel Window:Window Bounds, FP.WinBounds: docs-be
        ``properties-and-methods/vi-server/vi/fpwinbounds.html``): ``bounds`` is
        ``(left, top, right, bottom)`` in global screen coordinates. The function
        keeps the bounds for its own later reads."""
        return None

    def center_panel(self) -> None:
        """The function centered its own front panel window on the computer screen
        (Front Panel:Center, FP.Center: docs-be
        ``properties-and-methods/vi-server/vi/centerfrontpanel.html``). The
        function does not change the bounds it keeps: the screen's size is the
        window's to know."""
        return None

    def set_window_state(self, state: int) -> None:
        """The function wrote the State of its own front panel window (Front Panel
        Window:State, FP.State: docs-be ``properties-and-methods/vi-server/vi/
        fpstate.html``): 1 Standard, 3 Hidden, 4 Minimized or 5 Maximized (the
        window was open: a write on a closed window is an error the function
        reports). The function keeps the state for its own later reads."""
        return None

    def bring_to_front(self) -> None:
        """The function wrote TRUE to the Is Frontmost property of its own front
        panel window (Front Panel Window:Is Frontmost, FP.IsFrontmost: docs-be
        ``properties-and-methods/vi-server/vi/fpwinisfrontmost.html``): "Writing
        TRUE to this value brings the front panel to the front"."""
        return None

    def is_frontmost(self, held: bool) -> bool:
        """Whether the function's own front panel window is the front window
        (NI: "If you read this property, it indicates whether the front panel
        window is the front window (ignoring floating windows)"). ``held`` is what
        the function knows (True after it brought the window to the front or
        opened it as the active window); the base class returns it, the window
        system is what knows for sure."""
        return held

    def set_panel_bounds(self, bounds: tuple[int, int, int, int]) -> None:
        """The function wrote the Panel Bounds of its own front panel window (Front
        Panel Window:Panel Bounds, FP.PanelBounds: docs-be
        ``properties-and-methods/vi-server/vi/fpwinpanelbounds.html``):
        ``(left, top, right, bottom)`` of the interior of the front panel in global
        screen coordinates."""
        return None

    def set_panel_origin(self, origin: tuple[int, ...]) -> None:
        """The function wrote the Origin of its own front panel (Front Panel
        Window:Origin): the position the panel is scrolled to, the coordinates of
        its upper left corner as the Pane class's Origin gives them (docs-be
        ``properties-and-methods/vi-server/generic/gobject/pane/origin.html``),
        in the order of the cluster the function wrote."""
        return None

    def set_window_monitor(self, monitor: int) -> None:
        """The function wrote the Monitor of its own front panel window (Front
        Panel Window:Monitor, FP.Monitor: docs-be
        ``properties-and-methods/vi-server/vi/fpmonitor.html``): "The value 0 is the
        primary monitor" on Windows."""
        return None

    def set_window_behavior(self, behavior: int) -> None:
        """The function wrote the Behavior of its own front panel window (Front
        Panel Window:Behavior, FP.Behavior: docs-be
        ``properties-and-methods/vi-server/vi/fpbehavior.html``): 1 Default, 2
        Floating, 3 Floating/Auto-Hide, 4 Modal."""
        return None

    def monitors(self) -> list[tuple[int, int, int, int, int]]:
        """The monitors of the computer, ``(left, top, right, bottom, depth)`` each
        (Display:All Monitors, Disp.AllMonitors: docs-be ``properties-and-methods/
        vi-server/application/dispallmon.html``: "information about all the
        monitors on the computer, including their bit depths and bounding
        rectangles"). The base class has no screen: it gives no monitors."""
        return []

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
