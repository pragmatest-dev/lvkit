"""Code generator for Event Structures.

An Event Structure waits for an event of the front panel and runs the frame of the
event that came. A VI that holds one takes a queue of the panel's events as
``events`` (``codegen/event_kinds.py``), and the structure reads its next message
from it::

    if events is None:
        events = Queue()
    try:
        event = events.get(timeout=50 / 1000)
    except Empty:
        event = None
    if event is None:
        ...                           # the Timeout frame
    elif event[2] == Kind.VALUE_CHANGE and event[0] == Name.GAIN:
        ...                           # a Value Change frame; its NewVal is event[1]
    elif len(event) > 4:
        event[4].put((False, None))   # no frame answers this filter event

The queue staying empty for the time of the Timeout terminal (-1 or unwired: for
ever) is the Timeout. A frame is chosen by the control and the kind of event the
file records for it, never by its label. The frame of a filter event (Panel
Close?) ends by answering on the message's reply queue with the Event Filter
Node's Discard?. A VI called with no ``events`` and no Timeout would wait for ever
for a panel that is not there: the generated code raises where it would wait, and
generation records that as a gap.

Only Timeout, Value Change on a control and Panel Close? are handled; a structure
with any other event is a gap that names it.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode, EventStructureNode
from lvkit.models import EventFrame, Terminal, TunnelTerminal
from lvkit.parser.constants import (
    NODE_CLASS_EVENT_DATA_NODE,
    TUNNEL_CLASS_EVENT_TIMEOUT,
)

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..context import CodeGenContext
from ..event_kinds import (
    KIND,
    NAME,
    REPLY,
    TIMEOUT_CODE,
    VALUE_CHANGE_CODE,
    MessageKind,
    message_kind,
    unsupported_events,
)
from ..fragment import CodeFragment
from ..gaps import record_gap
from ..panel_hooks import PanelHooks
from .frame_structure import (
    bind_input_tunnels,
    frame_operations,
    generate_frame_body,
    resolve_output_tunnels,
)

_WAITS_FOR_EVENT = (
    "The Event Structure waits for a front-panel event and has no Timeout: "
    "pass events= to run it"
)
_EVENTS = "events"


def generate(node: EventStructureNode, ctx: CodeGenContext) -> CodeFragment:
    """The code of an Event Structure: read the next message, run the frame of its
    event."""
    hooks = ctx.panel_hooks
    if hooks is None or hooks.event_enum_name is None:
        raise ConversionGap("An Event Structure needs the events of the panel", node)
    _require_handled(node, hooks)
    message = ctx.make_output_var("event", node.id)
    ctx.event_messages[node.id] = message
    bind_input_tunnels(node, ctx)

    operations = frame_operations(node, ctx)
    diagram = {
        key: [n for n in ops if not _is_data_node(n)]
        for key, ops in operations.items()
    }
    bodies: list[tuple[EventFrame, list[ast.stmt]]] = []
    for frame in node.frames:
        data_nodes = [n for n in operations[id(frame)] if _is_data_node(n)]
        reads = _event_data(node, frame, data_nodes, ctx, message)
        body = [*reads, *generate_frame_body(node, frame, diagram, ctx).statements]
        body.extend(_reply(node, frame, data_nodes, ctx, message, hooks))
        bodies.append((frame, body))
    bindings, pre_decls = resolve_output_tunnels(node, bodies, ctx)

    timeout_body: list[ast.stmt] = []
    branches: list[tuple[ast.expr, list[ast.stmt]]] = []
    for frame, body in bodies:
        if _is_timeout(frame):
            timeout_body = body
        else:
            branches.append((_frame_test(frame, message, hooks), body))
    unanswered = ast.If(
        test=parse_expr(f"len({message}) > {REPLY}"),
        body=[_statement(f"{message}[{REPLY}].put((False, None))")],
        orelse=[],
    )
    chain: list[ast.stmt] = [unanswered]
    for test, body in reversed(branches):
        chain = [ast.If(test=test, body=body or [ast.Pass()], orelse=chain)]
    dispatch = ast.If(
        test=parse_expr(f"{message} is None"),
        body=timeout_body or [ast.Pass()],
        orelse=chain,
    )
    return CodeFragment(
        statements=[*pre_decls, *_receive(node, ctx, message), dispatch],
        bindings=bindings,
    )


def _statement(source: str) -> ast.stmt:
    return ast.parse(source).body[0]


def _is_data_node(node: AnyGraphNode) -> bool:
    """Whether ``node`` is an Event Data Node or an Event Filter Node."""
    return node.node_type == NODE_CLASS_EVENT_DATA_NODE


def _is_timeout(frame: EventFrame) -> bool:
    return all(e.type_code == TIMEOUT_CODE for e in frame.events)


def _gap(
    node: EventStructureNode, frame: EventFrame | None, reason: str
) -> ConversionGap:
    where = f" (frame {frame.event_label})" if frame is not None else ""
    return ConversionGap(f"{reason}{where}", node)


def _require_handled(node: EventStructureNode, hooks: PanelHooks) -> None:
    """Raise the gap of a structure with an event the code does not handle."""
    unhandled = unsupported_events(node.frames)
    if unhandled:
        raise ConversionGap(
            "The Event Structure handles Timeout, Value Change on a control and "
            f"Panel Close? only; it also handles {', '.join(unhandled)}",
            node,
        )
    for frame in node.frames:
        if not frame.events:
            raise _gap(node, frame, "The frame lists no event")
        if TIMEOUT_CODE in {e.type_code for e in frame.events} and not _is_timeout(
            frame
        ):
            raise _gap(node, frame, "The frame handles Timeout and other events")
        for event in frame.events:
            if event.type_code == VALUE_CHANGE_CODE and (
                event.source_terminal_id not in hooks.names
            ):
                raise _gap(
                    node, frame, "The Value Change is on a control without a terminal"
                )


def _event_member(hooks: PanelHooks, kind: MessageKind) -> str:
    return f"{hooks.event_enum_name}.{kind.member}"


def _frame_test(frame: EventFrame, message: str, hooks: PanelHooks) -> ast.expr:
    """The condition that ``message`` is an event ``frame`` handles: the kind, and
    for a Value Change the control it comes from."""
    clauses: list[str] = []
    for kind in dict.fromkeys(
        k for k in (message_kind(e) for e in frame.events) if k is not None
    ):
        test = f"{message}[{KIND}] == {_event_member(hooks, kind)}"
        if kind.code == VALUE_CHANGE_CODE:
            names = list(
                dict.fromkeys(
                    ast.unparse(hooks.member(e.source_terminal_id))
                    for e in frame.events
                    if e.type_code == kind.code and e.source_terminal_id
                )
            )
            test += (
                f" and {message}[{NAME}] == {names[0]}"
                if len(names) == 1
                else f" and {message}[{NAME}] in ({', '.join(names)})"
            )
        clauses.append(f"({test})" if len(frame.events) > 1 else test)
    return parse_expr(" or ".join(clauses))


def _receive(
    node: EventStructureNode, ctx: CodeGenContext, message: str
) -> list[ast.stmt]:
    """The statements that read the next message into ``message``, or leave None
    when the Timeout passes first."""
    timeout = next(
        (
            t
            for t in node.terminals
            if isinstance(t, TunnelTerminal)
            and t.tunnel_type == TUNNEL_CLASS_EVENT_TIMEOUT
            and t.boundary == "outer"
        ),
        None,
    )
    wired = ctx.wired_input(timeout.id, node) if timeout is not None else None
    literal = _integer(wired)
    if wired is None or (literal is not None and literal < 0):
        wait = record_gap(ctx, node, _WAITS_FOR_EVENT)
        return [
            ast.If(test=parse_expr(f"{_EVENTS} is None"), body=[wait], orelse=[]),
            _statement(f"{message} = {_EVENTS}.get()"),
        ]
    seconds = (
        f"{literal} / 1000"
        if literal is not None
        else f"None if {wired} < 0 else {wired} / 1000"
    )
    return [
        _statement(f"if {_EVENTS} is None:\n    {_EVENTS} = Queue()"),
        _statement(
            f"try:\n    {message} = {_EVENTS}.get(timeout={seconds})\n"
            f"except Empty:\n    {message} = None"
        ),
    ]


def _integer(text: str | None) -> int | None:
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None


def _field_name(data_node: AnyGraphNode, term: Terminal) -> str | None:
    """The name the cluster of ``data_node`` gives the field ``term`` carries, as
    the VI's own type records it."""
    aggregate = next((t for t in data_node.terminals if t.nmux_role == "agg"), None)
    fields = aggregate.lv_type.fields if aggregate and aggregate.lv_type else None
    index = term.nmux_field_index
    if not fields or index is None or not 0 <= index < len(fields):
        return None
    return fields[index].name


def _kinds(frame: EventFrame) -> list[MessageKind]:
    return list(
        dict.fromkeys(k for k in (message_kind(e) for e in frame.events) if k)
    )


def _event_data(
    node: EventStructureNode,
    frame: EventFrame,
    data_nodes: list[AnyGraphNode],
    ctx: CodeGenContext,
    message: str,
) -> list[ast.stmt]:
    """The assignments at the start of the frame that take each output of its
    Event Data Node that is wired from the item of the message that holds the
    field (and write the indicators it feeds); a field the message does not carry
    is a gap. A field is found by the name its cluster gives it: where it sits in
    the cluster differs from one control to another."""
    held = dict(entry for kind in _kinds(frame) for entry in kind.data)
    statements: list[ast.stmt] = []
    for data_node in data_nodes:
        if data_node.id in node.filter_node_uids:
            continue
        for term in data_node.terminals:
            if term.nmux_role != "list" or term.direction != "output":
                continue
            if not ctx.is_wired(term.id):
                continue
            name = _field_name(data_node, term)
            if name is None or name not in held:
                raise _gap(
                    node,
                    frame,
                    f"The frame reads the Event Data Node field {name or '?'}, "
                    "which a front-panel event does not carry",
                )
            variable = ctx.make_output_var(
                to_var_name(name), data_node.id, terminal_id=term.id
            )
            ctx.bind(term.id, variable)
            statements.append(
                build_assign(variable, parse_expr(f"{message}[{held[name]}]"))
            )
        statements.extend(ctx.local_indicators.assignments_after(data_node, ctx))
    return statements


def _reply(
    node: EventStructureNode,
    frame: EventFrame,
    data_nodes: list[AnyGraphNode],
    ctx: CodeGenContext,
    message: str,
    hooks: PanelHooks,
) -> list[ast.stmt]:
    """The answer to a filter event at the end of its frame: the Event Filter
    Node's Discard? (False when the node is not there or Discard? is not wired)
    and no modified value."""
    filters = [k for k in _kinds(frame) if k.filter]
    if not filters:
        return []
    discard = "False"
    for data_node in data_nodes:
        if data_node.id not in node.filter_node_uids:
            continue
        for term in data_node.terminals:
            if term.nmux_role != "list" or term.direction != "input":
                continue
            if not ctx.is_wired(term.id):
                continue
            name = _field_name(data_node, term)
            if name is None or name not in {k.discard for k in filters}:
                raise _gap(
                    node,
                    frame,
                    f"The Event Filter Node input {name or '?'} is not handled",
                )
            discard = ctx.wired_input(term.id, data_node) or "False"
    statement = _statement(f"{message}[{REPLY}].put(({discard}, None))")
    if all(message_kind(e) in filters for e in frame.events):
        return [statement]
    test = " or ".join(
        f"{message}[{KIND}] == {_event_member(hooks, k)}" for k in filters
    )
    return [ast.If(test=parse_expr(test), body=[statement], orelse=[])]
