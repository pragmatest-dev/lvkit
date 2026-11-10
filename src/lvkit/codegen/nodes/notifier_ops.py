"""Code generator for the Notifier Operations primitives.

Covers Obtain Notifier (9101), Release Notifier (9102), Send Notification (9104)
and Wait on Notification (9105).

A notifier is the package's ``Notifier`` (``notifiers_module``): it keeps only the
latest message, wakes every function waiting on it when a message is sent, and
each Wait on Notification node waits for the next message it has not received,
which no ``queue`` or ``threading.Event`` does. The class is written once into
the package and imported from there.

Obtain Notifier with no name is a new ``Notifier``; with a name it is
``Notifier.obtain(name, create)``, which gives another reference to the one
notifier of that name in the program, and ``created new?`` says whether it made
it. Wait on Notification passes the name of its node (``site``) so the notifier
can tell whether this node has received the message it holds; a timeout is no
error (NI): ``timed out?`` is true and the notification is its type's default. A
notifier that was released raises ``ReferenceError``, which the catalog makes
error 1122 on Wait on Notification (NI) and ``timed out?`` true (``on_failure``).

These are generic ``<Prim class="prim">`` XML nodes distinguished only by
``primResID``, so dispatch happens by primResID in ``nodes/__init__.py``. Terminals
are resolved by the connector-pane INDEX recorded in the catalog. Error cluster
terminals are left to ``error_flow`` and ``native_failure``.
"""

from __future__ import annotations

import ast
from enum import IntEnum

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ..ast_utils import parse_expr, type_default_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..import_resolver import ImportResolver


class NotifierOp(IntEnum):
    """Notifier-operation ``primResID`` codes. ``IntEnum`` because the code IS the
    raw int carried on ``PrimitiveNode.prim_id``."""

    OBTAIN = 9101
    RELEASE = 9102
    SEND = 9104
    WAIT = 9105


# Membership set for the nodes/__init__ dispatcher.
NOTIFIER_PRIM_IDS = frozenset(NotifierOp)


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Dispatch a notifier primitive to its handler by primResID."""
    by_index: dict[int, Terminal] = {t.index: t for t in node.terminals}
    if node.prim_id == NotifierOp.OBTAIN:
        return _generate_obtain(node, by_index, ctx)
    if node.prim_id == NotifierOp.RELEASE:
        return _generate_release(node, by_index, ctx)
    if node.prim_id == NotifierOp.SEND:
        return _generate_send(node, by_index, ctx)
    if node.prim_id == NotifierOp.WAIT:
        return _generate_wait(node, by_index, ctx)
    raise ValueError(
        f"notifier_ops.generate() called for unsupported prim_id {node.prim_id!r}; "
        "the dispatch guard in codegen/nodes/__init__.py should only route "
        "NOTIFIER_PRIM_IDS here",
    )


def _input(
    term: Terminal | None, node: PrimitiveNode, ctx: CodeGenContext
) -> str | None:
    """The expression an input terminal carries, or None when it is unwired."""
    if term is None:
        return None
    value = ctx.wired_input(term.id, node)
    return value if value else None


def _consumed(term: Terminal | None, ctx: CodeGenContext) -> Terminal | None:
    """``term`` when something reads it, else None."""
    if term is not None and not term.is_error_cluster and ctx.is_wired(term.id):
        return term
    return None


def _notifier_input(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> str:
    notifier = _input(by_index.get(0), node, ctx)
    if notifier is None:
        raise ConversionGap(f"{node.name} has no notifier wired", node)
    return notifier


def _passthrough(by_index: dict[int, Terminal], notifier: str) -> dict[str, str]:
    """The ``notifier out`` output (index 8) is the notifier that went in."""
    term = by_index.get(8)
    return {term.id: notifier} if term is not None else {}


def _default(term: Terminal | None) -> str:
    """The zero value of the notification type of ``term``, as an expression."""
    return ast.unparse(type_default_expr(term.lv_type if term else None))


def _call(notifier: str, method: str, arguments: list[str]) -> ast.expr:
    return parse_expr(f"{notifier}.{method}({', '.join(arguments)})")


def _assign(names: list[str], value: ast.expr) -> ast.stmt:
    """``a = value``, or ``a, b = value`` for several names."""
    target: ast.expr = (
        ast.Name(id=names[0], ctx=ast.Store())
        if len(names) == 1
        else ast.Tuple(
            elts=[ast.Name(id=n, ctx=ast.Store()) for n in names], ctx=ast.Store()
        )
    )
    return ast.Assign(targets=[target], value=value)


def _site(node: PrimitiveNode, ctx: CodeGenContext) -> str:
    """The name of a Wait on Notification node in the package: its module's
    project path and its place in the diagram."""
    resolver = ctx.import_resolver
    where = ctx.vi_name or ""
    if isinstance(resolver, ImportResolver) and resolver.module_key is not None:
        where = resolver.shown(resolver.module_key)
    return f"{where}::{node.id.rsplit('::', 1)[-1]}"


def _generate_obtain(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> CodeFragment:
    """Obtain Notifier(name, element data type, create if not found?) ->
    (notifier out, created new?). ``element data type`` only carries a type."""
    name = _input(by_index.get(0), node, ctx)
    create = _input(by_index.get(2), node, ctx)
    notifier_term = _consumed(by_index.get(8), ctx)
    created_term = _consumed(by_index.get(10), ctx)
    ctx.import_notifier()
    bindings: dict[str, str] = {}
    notifier = ctx.make_output_var(
        "notifier", node.id, notifier_term.id if notifier_term is not None else None
    )
    created = ctx.make_output_var(
        "created", node.id, created_term.id if created_term is not None else None
    )
    if notifier_term is not None:
        bindings[notifier_term.id] = notifier
    if created_term is not None:
        bindings[created_term.id] = created
    if name is None:
        statements: list[ast.stmt] = []
        if notifier_term is not None:
            statements.append(_assign([notifier], parse_expr("Notifier()")))
        if created_term is not None:
            statements.append(_assign([created], ast.Constant(value=True)))
        return CodeFragment(statements=statements, bindings=bindings)
    arguments = [name] if create is None else [name, create]
    return CodeFragment(
        statements=[
            _assign([notifier, created], _call("Notifier", "obtain", arguments))
        ],
        bindings=bindings,
    )


def _generate_send(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> CodeFragment:
    """Send Notification(notifier, notification) -> notifier out."""
    notifier = _notifier_input(node, by_index, ctx)
    message = _input(by_index.get(1), node, ctx) or "None"
    return CodeFragment(
        statements=[ast.Expr(value=_call(notifier, "send", [message]))],
        bindings=_passthrough(by_index, notifier),
    )


def _generate_wait(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> CodeFragment:
    """Wait on Notification(notifier, ignore previous?, timeout in ms) ->
    (notifier out, notification, timed out?)."""
    notifier = _notifier_input(node, by_index, ctx)
    ignore = _input(by_index.get(1), node, ctx)
    timeout = _input(by_index.get(2), node, ctx)
    message_term = _consumed(by_index.get(9), ctx)
    timed_out_term = _consumed(by_index.get(10), ctx)
    bindings = _passthrough(by_index, notifier)
    message = ctx.make_output_var(
        "notification", node.id, message_term.id if message_term else None
    )
    timed_out = ctx.make_output_var(
        "timed_out", node.id, timed_out_term.id if timed_out_term else None
    )
    if message_term is not None:
        bindings[message_term.id] = message
    if timed_out_term is not None:
        bindings[timed_out_term.id] = timed_out
    arguments = [repr(_site(node, ctx))]
    if ignore is not None:
        arguments.append(f"ignore_previous={ignore}")
    if timeout is not None:
        arguments.append(f"timeout_ms={timeout}")
    if message_term is not None:
        arguments.append(f"default={_default(message_term)}")
    return CodeFragment(
        statements=[_assign([message, timed_out], _call(notifier, "wait", arguments))],
        bindings=bindings,
    )


def _generate_release(
    node: PrimitiveNode, by_index: dict[int, Terminal], ctx: CodeGenContext
) -> CodeFragment:
    """Release Notifier(notifier, force destroy?) -> (notifier name, last
    notification)."""
    notifier = _notifier_input(node, by_index, ctx)
    force = _input(by_index.get(2), node, ctx)
    name_term = _consumed(by_index.get(8), ctx)
    last_term = _consumed(by_index.get(9), ctx)
    bindings: dict[str, str] = {}
    name = ctx.make_output_var(
        "notifier_name", node.id, name_term.id if name_term else None
    )
    last = ctx.make_output_var(
        "last_notification", node.id, last_term.id if last_term else None
    )
    if name_term is not None:
        bindings[name_term.id] = name
    if last_term is not None:
        bindings[last_term.id] = last
    arguments: list[str] = []
    if force is not None:
        arguments.append(f"force={force}")
    if last_term is not None:
        arguments.append(f"default={_default(last_term)}")
    call = f"{notifier}.release({', '.join(arguments)})"
    # NI runs Release Notifier even when an error came in, so the Obtain Notifier
    # before it may have been skipped; there is then no notifier to release.
    if name_term is None and last_term is None:
        source = f"if {notifier} is not None:\n    {call}\n"
    else:
        none = _default(last_term) if last_term is not None else "None"
        source = (
            f"if {notifier} is not None:\n    {name}, {last} = {call}\n"
            f"else:\n    {name}, {last} = '', {none}\n"
        )
    return CodeFragment(statements=ast.parse(source).body, bindings=bindings)
