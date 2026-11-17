"""The Visible and Disabled state of a control the VI's diagram reads.

A Property Node row that reads ``Visible`` or ``Disabled`` of a control of its own
VI gives the state the control has now: the last value a Property Node of the VI
wrote, else the state the VI file stores for the control (``FPTerminal.starts_hidden``
and ``FPTerminal.enable_state``, read from the front panel's flags by
``parser/flags.py``). Only the VI's own Property Nodes change either property, so
a module variable, seeded to the stored state, holds it. Like an indicator a Local
Variable reads (``local_indicators``), the variable keeps its value from one call
of the VI to the next, as the control keeps its state in LabVIEW. A write of the
property assigns it as well as telling the panel (``live_io.set_property``).

A control whose stored state the file leaves open (the flags set one of the two
Disabled bits only) has no variable: its read stays a gap (``property_node``)."""

from __future__ import annotations

import ast

from lvkit.control_properties import control_property
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals
from lvkit.models import FPTerminal

from .ast_utils import build_assign, node_owned_name
from .context import CodeGenContext

STATE_MEMBERS = ("visible", "disabled")
"""The ``ControlProperty.member`` of the properties that start from the stored
state of the control."""


def starting_state(control: FPTerminal, member: str) -> bool | int | None:
    """The value the file stores for the property ``member`` of ``control``:
    ``Visible`` as a bool, ``Disabled`` as its enum value; None when the file does
    not record it (or ``member`` is not one of ``STATE_MEMBERS``)."""
    if member == "visible":
        return None if control.starts_hidden is None else not control.starts_hidden
    if member == "disabled":
        return control.enable_state
    return None


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
            ctx.add_state_global(name, ast.Constant(value=state), owner=bound.id)
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
