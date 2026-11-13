"""Code generator for the User Event primitives.

Covers Create User Event (2073), Generate User Event (2074), Destroy User Event
(2075) and Unregister For Events (2076). Register For Events is the node of
``event_register.py``.

A user event is a ``list`` of the queues of the registrations that hold it, and a
registration is the list ``[queue, rows, registered]`` (``event_register.py``)::

    stop = []                                    # Create User Event
    for queue in stop:                           # Generate User Event
        queue.put((stop, True, 'User Event', None))
    if registration is not None:                 # Unregister For Events
        for registered in registration[2]:
            if registration[0] in registered:
                registered.remove(registration[0])
    if stop is not None:                         # Destroy User Event
        stop.clear()

A message is the shape of ``event_kinds``: the user event itself (the list, not a
name) first, then its data, the kind ``'User Event'`` and no old value. The kind is
written as its text, so a VI with no Event Structure needs no enum for it. A user
event passes between VIs as the list. Unregister and Destroy run when an error came
in; a user event or registration that an earlier node did not make is ``None`` then,
and they do nothing for it. Unregister removes the queue only where it still is
(Destroy empties the list).

Priority: NI's Generate User Event documents ``0`` high and ``1`` normal (the
default); unwired or a constant ``1`` is converted, anything else is a gap.
"""

from __future__ import annotations

import ast
from enum import IntEnum

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ..ast_utils import COPY_IMPORT, build_assign, parse_stmt
from ..context import CodeGenContext
from ..event_kinds import QUEUE_IMPORT, USER_EVENT_NAME
from ..fragment import CodeFragment
from ..type_default import type_default
from ..value_copy import is_by_value_object

NORMAL_PRIORITY = 1


class UserEventOp(IntEnum):
    """User event ``primResID`` codes. ``IntEnum`` because the code IS the raw int
    carried on ``PrimitiveNode.prim_id``."""

    CREATE = 2073
    GENERATE = 2074
    DESTROY = 2075
    UNREGISTER = 2076


USER_EVENT_PRIM_IDS = frozenset(UserEventOp)

# The connector-pane indices of the terminals the generators read.
_CREATE_OUT = 8
_REFERENCE_IN = 0
_DATA_IN = 1
_PRIORITY_IN = 4
_GENERATE_OUT = 8


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Dispatch a user event primitive to its handler by primResID."""
    by_index = {t.index: t for t in node.terminals}
    if node.prim_id == UserEventOp.CREATE:
        return _create(node, by_index, ctx)
    if node.prim_id == UserEventOp.GENERATE:
        return _generate(node, by_index, ctx)
    if node.prim_id == UserEventOp.DESTROY:
        return _destroy(node, by_index, ctx)
    if node.prim_id == UserEventOp.UNREGISTER:
        return _unregister(node, by_index, ctx)
    raise ValueError(
        f"user_events.generate() called for unsupported prim_id {node.prim_id!r}"
    )


def _wired(
    node: PrimitiveNode, by_index: dict[int, Terminal], index: int, ctx: CodeGenContext
) -> str | None:
    term = by_index.get(index)
    return ctx.wired_input(term.id, node) if term is not None else None


def _required(
    node: PrimitiveNode,
    by_index: dict[int, Terminal],
    ctx: CodeGenContext,
    what: str,
) -> str:
    value = _wired(node, by_index, _REFERENCE_IN, ctx)
    if value is None:
        raise ConversionGap(f"{node.name} has no {what} wired", node)
    return value


def _create(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> CodeFragment:
    out = by_index.get(_CREATE_OUT)
    if out is None:
        return CodeFragment.empty()
    variable = ctx.make_output_var("user_event", node.id, terminal_id=out.id)
    return CodeFragment(
        statements=[build_assign(variable, ast.List(elts=[], ctx=ast.Load()))],
        bindings={out.id: variable},
    )


def _priority_gap(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> None:
    """Raise the gap of a priority other than normal."""
    value = _wired(node, by_index, _PRIORITY_IN, ctx)
    if value is None:
        return
    try:
        constant = ast.literal_eval(value)
    except (ValueError, SyntaxError):
        constant = None
    if constant != NORMAL_PRIORITY:
        raise ConversionGap(
            f"{node.name}'s priority is wired to something other than the "
            f"constant normal ({NORMAL_PRIORITY}); only normal priority is "
            "converted",
            node,
        )


def _generate(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> CodeFragment:
    event = _required(node, by_index, ctx, "user event")
    _priority_gap(node, by_index, ctx)
    data_term = by_index.get(_DATA_IN)
    data = _wired(node, by_index, _DATA_IN, ctx)
    if data is None:
        data = ast.unparse(
            type_default(ctx, data_term.lv_type if data_term is not None else None)
        )
    imports = {QUEUE_IMPORT}
    sent = data
    if data_term is not None and is_by_value_object(data_term.lv_type):
        # Each registered queue gets its own value, as LabVIEW copies the data.
        ctx.add_import(COPY_IMPORT)
        imports.add(COPY_IMPORT)
        sent = f"copy.deepcopy({data})"
    queue = ctx.make_output_var("queue", node.id)
    message = f"({event}, {sent}, {USER_EVENT_NAME!r}, None)"
    statements = [parse_stmt(f"for {queue} in {event}:\n    {queue}.put({message})")]
    bindings: dict[str, str] = {}
    out = by_index.get(_GENERATE_OUT)
    if out is not None:
        bindings[out.id] = event
    return CodeFragment(statements=statements, bindings=bindings, imports=imports)


def _destroy(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> CodeFragment:
    event = _required(node, by_index, ctx, "user event")
    statements = [parse_stmt(f"if {event} is not None:\n    {event}.clear()")]
    return CodeFragment(statements=statements)


def _unregister(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> CodeFragment:
    registration = _required(node, by_index, ctx, "event registration")
    return CodeFragment(statements=[unregister_statement(registration, ctx, node.id)])


def unregister_statement(
    registration: str, ctx: CodeGenContext, node_id: str
) -> ast.stmt:
    """Remove the queue of ``registration`` from every user event it holds, where
    it still is; nothing for a registration that is ``None``."""
    registered = ctx.make_output_var("registered", node_id)
    return parse_stmt(
        f"if {registration} is not None:\n"
        f"    for {registered} in {registration}[2]:\n"
        f"        if {registration}[0] in {registered}:\n"
        f"            {registered}.remove({registration}[0])"
    )


__all__ = ["USER_EVENT_PRIM_IDS", "UserEventOp", "generate", "unregister_statement"]
