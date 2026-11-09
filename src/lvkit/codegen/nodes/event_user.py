"""The user events of an Event Structure.

A registration is the list ``[queue, rows, registered]`` that Register For Events
makes (``event_register.py``). An Event Structure whose dynamic terminal is wired
reads the queue of that registration, and a frame that handles the user event of
row k is chosen by the identity of the event, never by a name::

    elif event[2] == Kind.USER_EVENT and event[0] is registration[1][k - 1]:

A row that is an array of user events is tested with ``any(event[0] is e for e in
registration[1][k - 1])``. The registration comes from its row in the type of the
wire on the dynamic terminal (``graph/event_registration.py``), counted across the
registrations of a cluster in field order.

Which queue a structure reads: the registration's own (``registration[0]``) when it
is the only one and the VI has no front-panel events. When the VI has some, or the
terminal carries a cluster of registrations, the structure reads the VI's
``events`` queue and points each registration to it (``events`` is the queue of
the whole VI, as for the front panel's events): it moves the messages waiting in the
registration's queue to ``events``, replaces the queue in each user event's list
(``registered``) and in the registration. That is idempotent, so a structure in a
loop does it at every pass.

The Event Data Node of a user event frame holds ``UsrEvtRef`` (``event[0]``) and the
fields of the event's data by name: each member of a cluster, or the one field of a
single value (``event[1]``).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.event_registration import (
    dynamic_terminal,
    locate_row,
    registration_rows,
)
from lvkit.graph.models import AnyGraphNode, EventStructureNode
from lvkit.models import EventFrame, EventRegRow, FrameEvent, LVType, LVTypeKind

from ..ast_utils import parse_stmt
from ..cluster_members import member_expression
from ..context import CodeGenContext
from ..event_kinds import (
    NAME,
    NEW_VALUE,
    TIMEOUT_CODE,
    USER_EVENT_CODE,
)

_EVENT_REG = "EventReg"
_USER_EVENT = "UserEvent"
EVENTS = "events"


@dataclass(frozen=True)
class DynamicRegistration:
    """What is wired to the dynamic terminal of an Event Structure: the expression
    of the wire, its type and the rows that type lists."""

    value: str
    lv_type: LVType
    rows: list[EventRegRow]


def has_static_events(ctx: CodeGenContext) -> bool:
    """Whether the VI has an Event Structure frame that handles an event of its own
    front panel (not a Timeout, not a dynamic one): the VI then takes the panel's
    events as ``events``, and every registration shares that queue."""
    if ctx.graph is None or ctx.vi_name is None:
        return False
    return any(
        isinstance(node, EventStructureNode)
        and any(
            event.dyn_index is None and event.type_code != TIMEOUT_CODE
            for frame in node.frames
            for event in frame.events
        )
        for node in ctx.graph.iter_nodes(ctx.vi_name)
    )


def dynamic_registration(
    node: EventStructureNode, ctx: CodeGenContext
) -> DynamicRegistration | None:
    """The registration wired to the structure's dynamic terminal; None when
    nothing is."""
    terminal = dynamic_terminal(node.terminals)
    if terminal is None or terminal.lv_type is None:
        return None
    value = ctx.wired_input(terminal.id, node)
    if value is None:
        return None
    return DynamicRegistration(
        value, terminal.lv_type, registration_rows(terminal.lv_type)
    )


def _registrations(
    node: AnyGraphNode, reg: DynamicRegistration, ctx: CodeGenContext
) -> list[str]:
    """The expression of each registration the wire carries: itself, or the fields
    of a cluster of registrations."""
    lv_type = reg.lv_type
    if lv_type.ref_type == _EVENT_REG:
        return [reg.value]
    if lv_type.kind == LVTypeKind.CLUSTER and lv_type.fields:
        if all(
            f.type is not None and f.type.ref_type == _EVENT_REG for f in lv_type.fields
        ):
            return [
                member_expression(ctx, lv_type, position, reg.value, node.id)
                for position in range(len(lv_type.fields))
            ]
    raise ConversionGap(
        "The Event Structure's dynamic terminal carries "
        f"{lv_type.type_descriptor()}, which is neither an event registration nor "
        "a cluster of them",
        node,
    )


def _row_entry(
    node: EventStructureNode,
    frame: EventFrame,
    event: FrameEvent,
    reg: DynamicRegistration | None,
    ctx: CodeGenContext,
) -> tuple[str, EventRegRow]:
    """The expression holding the row of the dynamic user event ``event`` (in
    ``rows`` of its registration) and the row; a gap when the registration has no
    User Event row for it."""
    where = f" (frame {frame.event_label})"
    index = event.dyn_index or 0
    if reg is None:
        raise ConversionGap(
            f"The frame handles dynamic event row {index}, but nothing is wired to "
            f"the Event Structure's dynamic terminal{where}",
            node,
        )
    located = locate_row(reg.lv_type, index)
    row = reg.rows[index - 1] if 0 < index <= len(reg.rows) else None
    if located is None or row is None or row.event_type != USER_EVENT_CODE:
        raise ConversionGap(
            f"The Event Structure frame handles dynamic event row {index}, but the "
            f"registration on its dynamic terminal has no User Event row {index}"
            f"{where}",
            node,
        )
    position, local = located
    registration = (
        reg.value
        if position is None
        else member_expression(ctx, reg.lv_type, position, reg.value, node.id)
    )
    return f"{registration}[1][{local - 1}]", row


def _is_array(row: EventRegRow) -> bool:
    return row.type is not None and row.type.kind == LVTypeKind.ARRAY


def require_handled(
    node: EventStructureNode,
    frame: EventFrame,
    reg: DynamicRegistration | None,
    ctx: CodeGenContext,
) -> None:
    """Raise the gap of a frame event that is dynamic and not a User Event of a
    row of the registration."""
    for event in frame.events:
        if event.dyn_index is None:
            continue
        if event.type_code != USER_EVENT_CODE:
            raise ConversionGap(
                "The frame handles a dynamic front-panel event "
                f"(frame {frame.event_label})",
                node,
            )
        _row_entry(node, frame, event, reg, ctx)


def user_event_test(
    node: EventStructureNode,
    frame: EventFrame,
    message: str,
    kind_test: str,
    reg: DynamicRegistration | None,
    ctx: CodeGenContext,
) -> str:
    """The condition that ``message`` is a user event ``frame`` handles: the kind,
    and the identity of the event among the rows of the registration."""
    tests: list[str] = []
    for event in frame.events:
        if event.type_code != USER_EVENT_CODE:
            continue
        entry, row = _row_entry(node, frame, event, reg, ctx)
        if _is_array(row):
            tests.append(f"any({message}[{NAME}] is e for e in {entry})")
        else:
            tests.append(f"{message}[{NAME}] is {entry}")
    joined = tests[0] if len(tests) == 1 else "(" + " or ".join(tests) + ")"
    return f"{kind_test} and {joined}"


def data_fields(
    node: EventStructureNode,
    frame: EventFrame,
    data_node: AnyGraphNode,
    message: str,
    reg: DynamicRegistration | None,
    ctx: CodeGenContext,
) -> dict[str, str]:
    """The Event Data Node fields of a user event frame that hold the event's
    data, each with the expression that reads it from ``message``: the members of
    a cluster by name, or the one field after ``UsrEvtRef`` of a single value."""
    event = next((e for e in frame.events if e.type_code == USER_EVENT_CODE), None)
    if event is None:
        return {}
    _, row = _row_entry(node, frame, event, reg, ctx)
    user_event = row.type
    if user_event is not None and user_event.kind == LVTypeKind.ARRAY:
        user_event = user_event.element_type
    data_type = user_event.element_type if user_event is not None else None
    data = f"{message}[{NEW_VALUE}]"
    if data_type is not None and data_type.kind == LVTypeKind.CLUSTER:
        return {
            field.name: member_expression(ctx, data_type, position, data, node.id)
            for position, field in enumerate(data_type.fields or [])
        }
    aggregate = next((t for t in data_node.terminals if t.nmux_role == "agg"), None)
    fields = aggregate.lv_type.fields if aggregate and aggregate.lv_type else None
    names = [f.name for f in fields or []]
    after = names[names.index("UsrEvtRef") + 1 :] if "UsrEvtRef" in names else []
    return {after[0]: data} if after else {}


def receive_queue(
    node: EventStructureNode, reg: DynamicRegistration | None, ctx: CodeGenContext
) -> tuple[str, list[ast.stmt]]:
    """The queue the structure reads its next message from, and the statements that
    come first: for the VI's ``events``, making it when none came in and pointing
    each registration to it."""
    if reg is None:
        return EVENTS, [make_events()]
    registrations = _registrations(node, reg, ctx)
    if len(registrations) == 1 and not has_static_events(ctx):
        return f"{registrations[0]}[0]", []
    statements = [make_events()]
    for registration in registrations:
        statements.append(
            _repoint(registration, ctx.make_output_var("registered", node.id))
        )
    return EVENTS, statements


def make_events() -> ast.stmt:
    """``if events is None: events = Queue()``."""
    return parse_stmt(f"if {EVENTS} is None:\n    {EVENTS} = Queue()")


def _repoint(registration: str, registered: str) -> ast.stmt:
    return parse_stmt(
        f"if {registration}[0] is not {EVENTS}:\n"
        f"    while not {registration}[0].empty():\n"
        f"        {EVENTS}.put({registration}[0].get())\n"
        f"    for {registered} in {registration}[2]:\n"
        f"        if {registration}[0] in {registered}:\n"
        f"            {registered}.remove({registration}[0])\n"
        f"        {registered}.append({EVENTS})\n"
        f"    {registration}[0] = {EVENTS}"
    )


__all__ = [
    "DynamicRegistration",
    "data_fields",
    "dynamic_registration",
    "has_static_events",
    "make_events",
    "receive_queue",
    "require_handled",
    "user_event_test",
]
