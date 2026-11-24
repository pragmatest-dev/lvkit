"""The Visible, Disabled and Strings[] state of a control the VI's diagram reads.

A Property Node row that reads ``Visible``, ``Disabled`` or a Combo Box's
``Strings[]`` of a control of its own VI gives the state the control has now: the
last value a Property Node of the VI wrote, else the state the VI file stores for
the control (``FPTerminal.starts_hidden`` and ``FPTerminal.enable_state``, read from
the front panel's flags by ``parser/flags.py``; a Combo Box's strings, its
``FPTerminal.enum_values``). The VI's own Property Nodes change these properties, so
a module variable, seeded to the stored state, holds it. Like an indicator a Local
Variable reads (``local_indicators``), the variable keeps its value from one call
of the VI to the next, as the control keeps its state in LabVIEW. A write of the
property assigns it as well as telling the panel (``live_io.set_property``).
In a VI that takes ``live_io`` the read is ``live_io.get_property(..., <the
variable>)``: the panel answers with the state it holds, and with no panel the
variable does.

A property the file saves and NI says a running VI cannot change (an Enum's
``Strings[]``, ``ControlProperty.settable_while_running`` False) has no variable: it
is ``saved_value``, a constant of the control, and the panel's ``saved`` keyword
(``saved_properties``) holds it for a reference to read.

A control whose stored state the file leaves open (the flags set the greyed bit
without the disabled bit, which no corpus file does) has no variable: its read
stays a gap (``property_node``)."""

from __future__ import annotations

import ast

from lvkit.control_properties import (
    SAVED_MEMBERS,
    STATE_MEMBERS,
    control_property,
    property_defaults,
    saved_defaults,
)
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals
from lvkit.models import FPTerminal

from .ast_utils import build_assign, node_owned_name
from .context import CodeGenContext

StateValue = (
    bool | int | float | str | tuple[int, ...] | list[int] | list[str] | list[list[str]]
)
"""A property's state: ``Visible`` a bool, ``Disabled`` its enum value, a Combo
Box's ``Strings[]`` the list of its drop-down strings, a number, a string, a cell
``(row, column)`` or a list of indexes or strings."""


def starting_state(control: FPTerminal, member: str) -> StateValue | None:
    """The value the file stores for the property ``member`` of ``control``:
    ``Visible`` as a bool, ``Disabled`` as its enum value, a Combo Box's
    ``Strings[]`` as its list of strings (the parser records the items of a
    Combo Box or a Ring in ``enum_values``); None when the file does not record it
    (or ``member`` is not one of ``STATE_MEMBERS``, or the control is neither for
    ``Strings[]``)."""
    if member == "visible":
        return None if control.starts_hidden is None else not control.starts_hidden
    if member == "disabled":
        return control.enable_state
    if member == "strings":
        if control.vi_server_class == "Ring" or (
            control.control_type == "stdComboBox" and not control.undecoded_strings
        ):
            return list(control.enum_values)
        return None
    return None


def saved_value(control: FPTerminal, member: str) -> StateValue | None:
    """The value of the property ``member`` that the file saves for ``control`` and
    a running VI cannot change (``ControlProperty.settable_while_running`` False): an
    Enum's ``Strings[]``, its items, and a Boolean's ``MechAction``, its Mechanical
    Action number; None for a control that has no such property (or whose file does
    not record it)."""
    if member == "strings" and control.vi_server_class == "Enum":
        return list(control.enum_values)
    if member == "mech_action" and control.control_type == "stdBool":
        return control.mechanical_action
    return None


def saved_properties(control: FPTerminal) -> dict[str, StateValue]:
    """Every property ``control`` is saved with that is not the default of its VI
    Server class (``control_properties.saved_defaults``), by member name: what the
    panel holds for a reference to read (``Panel`` ``saved=``)."""
    defaults = saved_defaults().get(control.vi_server_class or "", {})
    found: dict[str, StateValue] = {}
    for member in SAVED_MEMBERS:
        value = saved_value(control, member)
        if value is not None and value != defaults.get(member):
            found[member] = value
    return found


def element_saved_properties(control: FPTerminal) -> dict[str, StateValue]:
    """The ``saved_properties`` of the element of the array ``control``: an Enum
    element's ``Strings[]`` (the parser's ``enum_values`` of an array are its
    element's) and a Boolean element's ``MechAction``, leaving out one that is the
    default of the element's VI Server class."""
    element_class = control.element_vi_server_class
    found: dict[str, StateValue] = {}
    if element_class == "Enum":
        found["strings"] = list(control.enum_values)
    if (
        control.element_control_type == "stdBool"
        and control.element_mechanical_action is not None
    ):
        default = saved_defaults().get(element_class or "", {}).get("mech_action")
        if control.element_mechanical_action != default:
            found["mech_action"] = control.element_mechanical_action
    return found


def state_expression(state: StateValue) -> ast.expr:
    """The Python expression of the state ``state``."""
    if isinstance(state, list):
        return ast.List(
            elts=[state_expression(item) for item in state], ctx=ast.Load()
        )
    if isinstance(state, tuple):
        return ast.Tuple(
            elts=[state_expression(item) for item in state], ctx=ast.Load()
        )
    return ast.Constant(value=state)


def stored_states(control: FPTerminal) -> dict[str, StateValue]:
    """The properties of ``control`` that the file stores in a state other than
    the default of the property (``control_properties.json``: a control in
    LabVIEW's default state is visible, enabled, with no tip), and those with no
    default (a Combo Box's strings), by member name: what the panel starts the
    control in."""
    defaults = property_defaults()
    states: dict[str, StateValue] = {}
    for member in STATE_MEMBERS:
        state = starting_state(control, member)
        if state is not None and (member not in defaults or state != defaults[member]):
            states[member] = state
    return states


def entry_property_states(ctx: CodeGenContext) -> list[ast.stmt]:
    """The ``global`` statement of a VI that reads ``Visible`` or ``Disabled`` of
    one of its own controls with a stored state, and the module variable of each
    such control and property, registered in ``ctx.property_states``."""
    graph = ctx.graph
    if graph is None or ctx.vi_name is None:
        return []
    names: list[str] = []
    for node in graph.iter_nodes(ctx.vi_name):
        if not isinstance(node, PrimitiveNode) or not node.bound_control_uid:
            continue
        bound = graph.bound_terminal(node)
        if bound is None:
            continue
        for row, term in correlate_property_terminals(
            node.properties, node.terminals, node.property_value_terminal_ids
        ):
            if term is None or term.direction != "output":
                continue
            listed = control_property(
                node.object_name or "", [seg.code for seg in row.segments]
            )
            if listed is None:
                continue
            state = starting_state(bound, listed.member)
            key = (bound.id, listed.member)
            if state is None or key in ctx.property_states:
                continue
            name = node_owned_name(f"_lv_{listed.member}", ctx.function_name, bound.id)
            ctx.add_state_global(name, state_expression(state), owner=bound.id)
            ctx.property_states[key] = name
            names.append(name)
    if not names:
        return []
    return [ast.Global(names=sorted(names))]


def assign_state(
    ctx: CodeGenContext, terminal_id: str, member: str, value: ast.expr
) -> list[ast.stmt]:
    """The statement that stores a write of the property ``member`` of the
    control ``terminal_id`` in its variable; none when the VI never reads it."""
    name = ctx.property_states.get((terminal_id, member))
    if name is None:
        return []
    return [build_assign(name, value)]
