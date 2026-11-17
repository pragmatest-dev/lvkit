"""What a widget does when the running VI sets a property of its control.

A property is named by its member (``visible``, ``disabled``, ``key_focus``,
``minimum``, ``maximum``: the ``member`` of ``control_properties.json``, the value
the generated property enum carries), never by the text of its LabVIEW name. A
``Port`` holds one applier for each property its widget can show; the view calls it
on the event loop (``view.PanelWidgets.paint``). A property with no applier is one
the widget cannot show: the panel generator warns of it (``lvkit.panel.write``) and
the view raises where the VI sets it.

``Visible`` is the box of the control (its caption and its widget:
``PanelWidgets.box``), not an applier of the port.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nicegui import ui
from nicegui.elements.mixins.disableable_element import DisableableElement

VISIBLE = "visible"
DISABLED = "disabled"
KEY_FOCUS = "key_focus"
MINIMUM = "minimum"
MAXIMUM = "maximum"

# DisabledEnum (NI ``control/disabledenum.html``): 0 Enabled, 1 Disabled, 2 Disabled
# and grayed out.
ENABLED_STATE = 0
GREYED_OUT_STATE = 2
GREYED_OUT_CLS = "opacity-40"
# A disabled widget takes no pointer input, whatever it holds (an array's grid, an
# indicator light), as well as disabling the input elements inside it.
NO_INPUT_CLS = "pointer-events-none"

Applier = Callable[[Any], object]


def disabled_applier(*widgets: Any) -> Applier:
    """Disabled 0 enables the widgets, 1 disables them and 2 disables them and
    greys them out. A widget that holds others (a cluster, an array, an error
    cluster) applies it to every input element inside it."""

    def apply(value: Any) -> None:
        state = int(value)
        for widget in widgets:
            for element in widget.descendants(include_self=True):
                if isinstance(element, DisableableElement):
                    if state == ENABLED_STATE:
                        element.enable()
                    else:
                        element.disable()
            if state == ENABLED_STATE:
                widget.classes(remove=NO_INPUT_CLS)
            else:
                widget.classes(add=NO_INPUT_CLS)
            if state == GREYED_OUT_STATE:
                widget.classes(add=GREYED_OUT_CLS)
            else:
                widget.classes(remove=GREYED_OUT_CLS)

    return apply


def focus_applier(widget: Any, *, root: bool = False) -> Applier:
    """Key Focus True gives the widget the keyboard focus, False takes it away.
    ``root`` is for a widget whose DOM element is the focusable one (a switch has
    no ``focus`` method); an input or a select has a ``focus`` method of its own."""

    def apply(value: Any) -> None:
        action = "focus" if value else "blur"
        if root:
            ui.run_javascript(f"getElement({widget.id}).$el.{action}()")
        else:
            widget.run_method(action)

    return apply


def limit_applier(widget: Any, attribute: str) -> Applier:
    """Minimum or Maximum sets the number box's limit (``attribute`` is ``min`` or
    ``max``)."""

    def apply(value: Any) -> None:
        setattr(widget, attribute, float(value))

    return apply


def widget_properties(
    *widgets: Any,
    focusable: Any = None,
    root_focus: bool = False,
    limits: Any = None,
) -> dict[str, Applier]:
    """The appliers of a port drawn by ``widgets``: Disabled on all of them, Key
    Focus when ``focusable`` (the widget that takes the focus) is given, Minimum
    and Maximum when ``limits`` (a number box) is."""
    found: dict[str, Applier] = {DISABLED: disabled_applier(*widgets)}
    if focusable is not None:
        found[KEY_FOCUS] = focus_applier(focusable, root=root_focus)
    if limits is not None:
        found[MINIMUM] = limit_applier(limits, "min")
        found[MAXIMUM] = limit_applier(limits, "max")
    return found
