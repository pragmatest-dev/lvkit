"""Code generator for Register For Events.

The node registers the user events wired to its rows. It makes the registration, the
list ``[queue, rows, registered]``::

    registration = [Queue(), (stop, None, cluster.a), (stop, cluster.a)]
    for registered in registration[2]:
        registered.append(registration[0])

- ``queue`` is a new ``Queue``, the VI's ``events`` when the VI has front-panel
  events (one ``get`` then serves both), or the queue of the registration wired to
  the node's input.
- ``rows`` has one item for each index a frame's ``dynIndex`` can name, in the
  numbering of ``graph/event_registration.py``: a user event, a tuple of the events
  of a row that is an array (taken when the node runs), and ``None`` at the index of
  a cluster row itself, which is followed by its members.
- ``registered`` is every user event that holds the queue (the elements of an array
  row listed one by one), so Unregister and a second Register need no layout.

A registration wired to the input is registered again: the queue stays, the rows are
replaced (the old events lose the queue, the new ones get it). A row that registers
anything but a User Event (a control, VI or application reference, which are the
dynamic front-panel events) is a gap.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind

from ..ast_utils import build_assign, parse_expr, parse_stmt
from ..cluster_members import member_expression
from ..context import CodeGenContext
from ..error_flow import own_error_terminals
from ..event_kinds import QUEUE_IMPORT
from ..fragment import CodeFragment
from ..native_failure import automatic_error_handling, labview_error
from .event_user import events_var, has_static_events, make_events
from .user_events import unregister_statement

_USER_EVENT = "UserEvent"
_REGISTRATION_IN = 0
_REGISTRATION_OUT = 1

# LabVIEW's "Object reference is invalid" (0x41F).
INVALID_REFERENCE_CODE = 1055


def generate_register(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The registration of the user events on the rows of the node."""
    by_index = {t.index: t for t in node.terminals}
    by_id = {t.id: t for t in node.terminals}
    out = by_index.get(_REGISTRATION_OUT)
    reg_in = by_index.get(_REGISTRATION_IN)
    old = ctx.wired_input(reg_in.id, node) if reg_in is not None else None
    rows: list[ast.expr] = []
    registered: list[ast.expr] = []
    for number, row_id in enumerate(node.event_row_terminal_ids, start=1):
        row = by_id[row_id]
        value = ctx.wired_input(row_id, node)
        if value is None:
            raise ConversionGap(
                f"Register For Events row {number} has no user event wired", node
            )
        _items(node, number, row.lv_type, value, rows, registered, ctx)

    statements: list[ast.stmt] = []
    if old is not None:
        queue = f"{old}[0]"
    elif has_static_events(ctx):
        queue = events_var(ctx)
        statements.append(make_events(queue))
    else:
        queue = "Queue()"
    registration = ctx.make_output_var(
        "registration", node.id, terminal_id=out.id if out is not None else None
    )
    made = ast.List(
        elts=[
            ast.parse(queue, mode="eval").body,
            ast.Tuple(elts=rows, ctx=ast.Load()),
            ast.Tuple(elts=registered, ctx=ast.Load()),
        ],
        ctx=ast.Load(),
    )
    statements.append(build_assign(registration, made))
    if old is not None:
        statements.append(unregister_statement(old, ctx, node.id))
    each = ctx.make_output_var("registered", node.id)
    register = parse_stmt(
        f"for {each} in {registration}[2]:\n    {each}.append({registration}[0])"
    )
    bindings = {out.id: registration} if out is not None else {}
    fragment = CodeFragment(
        statements=statements, bindings=bindings, imports={QUEUE_IMPORT}
    )
    # A user event that is Not a Refnum cannot be registered: error 1055,
    # "Object reference is invalid" (LabVIEW's message for 0x41F; NI forums, "One
    # of the cluster elements was undefined and it broke the execution of the
    # node": forums.ni.com/t5/LabVIEW/event-registration-event-handling-
    # weirdness/m-p/3164572). No event is registered then. With no error out
    # wired the VI's Automatic Error Handling decides, as for a native failure:
    # on, the error is raised; off, nothing is registered and the run continues.
    invalid_events = f"any({each} is None for {each} in {registration}[2])"
    wired = any(ctx.is_wired(t.id) for t in own_error_terminals(node, ctx)[1])
    if not wired and not automatic_error_handling(ctx):
        fragment.statements.append(
            ast.If(
                test=parse_expr(f"not {invalid_events}"), body=[register], orelse=[]
            )
        )
        return fragment
    ctx.import_labview_error()
    invalid = labview_error(
        INVALID_REFERENCE_CODE,
        node.label or node.name or "Register For Events",
        "'a user event is Not a Refnum'",
    )
    if wired:
        own_error = ctx.make_output_var("own_error", node.id)
        fragment.statements.append(
            ast.If(
                test=parse_expr(invalid_events),
                body=[build_assign(own_error, invalid)],
                orelse=[register, build_assign(own_error, parse_expr("None"))],
            )
        )
        fragment.own_error = own_error
    else:
        fragment.statements.append(
            ast.If(
                test=parse_expr(invalid_events),
                body=[ast.Raise(exc=invalid, cause=None)],
                orelse=[register],
            )
        )
    return fragment


def _items(
    node: PrimitiveNode,
    number: int,
    lv_type: LVType | None,
    value: str,
    rows: list[ast.expr],
    registered: list[ast.expr],
    ctx: CodeGenContext,
) -> None:
    """Add the indices ``value`` of type ``lv_type`` takes: one for a user event, one
    for an array of them, and for a cluster its own (``None``) and its members'."""
    expression = ast.parse(value, mode="eval").body
    if lv_type is not None and lv_type.ref_type == _USER_EVENT:
        rows.append(expression)
        registered.append(expression)
        return
    if (
        lv_type is not None
        and lv_type.kind == LVTypeKind.ARRAY
        and lv_type.element_type is not None
        and lv_type.element_type.ref_type == _USER_EVENT
    ):
        rows.append(
            ast.Call(
                func=ast.Name(id="tuple", ctx=ast.Load()),
                args=[expression],
                keywords=[],
            )
        )
        registered.append(ast.Starred(value=expression, ctx=ast.Load()))
        return
    if lv_type is not None and lv_type.kind == LVTypeKind.CLUSTER and lv_type.fields:
        rows.append(ast.Constant(value=None))
        for position, member in enumerate(lv_type.fields):
            read = member_expression(ctx, lv_type, position, value)
            _items(node, number, member.type, read, rows, registered, ctx)
        return
    descriptor = lv_type.type_descriptor() if lv_type is not None else "value"
    raise ConversionGap(
        f"Register For Events row {number} registers a {descriptor} (dynamic "
        "front-panel events); only User Event rows are converted",
        node,
    )
