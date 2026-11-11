"""Which nodes run when an error comes in on their error in.

An error wire is a value: ``None`` when no error occurred, else a
``LabVIEWError`` whose ``status`` says whether it is an error (``True``) or a
warning (``False``). A node on an error chain (an error in and an error out)
skips its work when an error occurred, as LabVIEW's nodes do, and passes the
incoming error on to its error out; a warning does not skip it. So the node's
statements run inside ``if err is None or not err.status:``, its outputs take
their type's default when it skips (``type_default.type_default``), and an
error out the node does not set itself is its error in.

A node is not skipped by its caller when its own code reads the error in: a
subVI or method call passes it to the called function, which decides
(``CodeFragment.runs_on_error``); a node the catalog marks ``runs_on_error``
(NI: it runs even if an error occurred) runs always, and its error out is the
incoming error, else its own (``first_error``). Call By Reference is skipped on
its own error in, not the called VI's (``own_error_terminals``). A structure is
never skipped as a whole; the nodes inside it are. Consecutive nodes on one error
chain share one ``if`` (``share_guards``). Merge Errors combines its inputs
(``merge_errors``).
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING

from lvkit.graph.models import AnyGraphNode, PrimitiveNode, StructureNode
from lvkit.graph.vi_reference import CALL_ERROR_IN, CALL_ERROR_OUT
from lvkit.models import Terminal
from lvkit.parser.constants import NODE_CLASS_CALL_BY_REF

from .ast_utils import build_assign, parse_expr, to_var_name
from .fragment import CodeFragment
from .type_default import type_default

if TYPE_CHECKING:
    from .context import CodeGenContext

# The attribute that marks an ``if`` statement as the guard of a node on an error
# chain, holding the error it tests.
_GUARD = "lvkit_error_guard"


def error_occurred(error: str) -> str:
    """The condition that an error occurred on the error wire ``error`` (a
    warning is not one); ``False`` for the wire that is ``None``."""
    if error == "None":
        return "False"
    return f"{error} is not None and {error}.status"


def status_read(value: str, terminal_id: str, ctx: CodeGenContext) -> str:
    """``value``, wired to an input NI says takes only the status of an error
    cluster wired to it (And, Or, Not, Select's ``s``, Compound Arithmetic, …):
    whether an error occurred when an error cluster is wired there (a warning is
    not one), else ``value`` itself."""
    if ctx.graph is not None and ctx.graph.terminal_fed_by_error_cluster(terminal_id):
        return error_occurred(value)
    return value


def no_error_occurred(error: str) -> str:
    """The condition a node on the error wire ``error`` runs under: no error
    occurred (no error at all, or a warning)."""
    return f"{error} is None or not {error}.status"


class ErrorFieldRole(Enum):
    """Which field of an error cluster a slot is."""

    STATUS = "status"
    CODE = "code"
    SOURCE = "source"

    def in_error(self, error: str | None) -> str:
        """The expression for this field of the error on the wire ``error``;
        a wire that holds no error (``None``) has status False, code 0 and an
        empty source."""
        if self is ErrorFieldRole.STATUS:
            return error_occurred(error or "None")
        none = "0" if self is ErrorFieldRole.CODE else "''"
        if error is None or error == "None":
            return none
        return f"{error}.{self.value} if {error} is not None else {none}"


def error_terminals(node: AnyGraphNode, direction: str) -> list[Terminal]:
    """The terminals of ``node`` in ``direction`` that carry an error cluster."""
    return [
        t for t in node.terminals if t.direction == direction and t.is_error_cluster
    ]


def wire_typed_terminal_ids(node: AnyGraphNode, ctx: CodeGenContext) -> set[str]:
    """The ids of the terminals of the primitive ``node`` whose catalog type is
    the type of what is wired to them (``PrimitiveTerminal.takes_wire_type``)."""
    if not isinstance(node, PrimitiveNode) or node.prim_id is None:
        return set()
    resolved = ctx.primitive_catalog.resolve(
        prim_id=node.prim_id,
        node_slots={(t.index, t.direction) for t in node.terminals},
    )
    if resolved is None:
        return set()
    wire_typed = {
        (t.index, t.direction) for t in resolved.terminals if t.takes_wire_type
    }
    return {t.id for t in node.terminals if (t.index, t.direction) in wire_typed}


def own_error_terminals(
    node: AnyGraphNode, ctx: CodeGenContext
) -> tuple[Terminal | None, list[Terminal]]:
    """The error in and error outs of ``node`` itself: the error that skips its
    work and the outputs that pass that error on.

    A Call By Reference or Start Asynchronous Call node has its own error in and
    error out beside the called VI's connector pane; the called VI's error
    terminals are parameters and results of the call like any other. Any other
    node's error in is its one error cluster input (none when it has several, as
    Merge Errors does) and its error outs are its error cluster outputs. A
    primitive terminal whose catalog type is polymorphic takes its wire's type,
    so an error cluster on it is data (Variant To Data's ``type`` and ``data``),
    never the node's own error in or out."""
    if node.node_type == NODE_CLASS_CALL_BY_REF:
        error_in = next(
            (
                t
                for t in node.terminals
                if t.direction == "input" and t.index == CALL_ERROR_IN
            ),
            None,
        )
        outs = [
            t
            for t in node.terminals
            if t.direction == "output" and t.index == CALL_ERROR_OUT
        ]
        return error_in, outs
    data = wire_typed_terminal_ids(node, ctx)
    inputs = [t for t in error_terminals(node, "input") if t.id not in data]
    outputs = [t for t in error_terminals(node, "output") if t.id not in data]
    return (inputs[0] if len(inputs) == 1 else None), outputs


def guard(
    node: AnyGraphNode, fragment: CodeFragment, ctx: CodeGenContext
) -> CodeFragment:
    """``fragment``, the code of ``node``, run only when no error came in on the
    node's error in, and its error outs that the code does not set bound to the
    incoming error (``own_error_terminals``). A node whose work produced an error
    of its own (``CodeFragment.own_error``) reports it only when no error came
    in. A node with several error inputs (Merge Errors) combines them in its own
    code."""
    if isinstance(node, StructureNode) or ctx.is_feedback_node(node):
        # A Feedback Node stores whatever it is given, an error cluster too.
        return fragment
    error_in, outputs = own_error_terminals(node, ctx)
    incoming = (
        ctx.wired_input(error_in.id, node) if error_in is not None and outputs else None
    )
    error = incoming or "None"
    if fragment.own_error is not None:
        incoming_error_wins(outputs, fragment, error, ctx, node)
    for term in outputs:
        fragment.bindings.setdefault(term.id, error)
    if incoming is None or incoming == "None" or fragment.runs_on_error:
        return fragment
    return _skipped_on_error(node, fragment, incoming, ctx)


def first_error(incoming: str, own: str) -> str:
    """The error out of a node that runs despite an incoming error: ``incoming``
    when an error occurred on it, else the node's ``own`` error, else (``own`` is
    ``None``) ``incoming``, which may be a warning."""
    if incoming == "None":
        return own
    return (
        f"{incoming} if {error_occurred(incoming)} "
        f"else ({own} if {own} is not None else {incoming})"
    )


def incoming_error_wins(
    outputs: list[Terminal],
    fragment: CodeFragment,
    incoming: str,
    ctx: CodeGenContext,
    node: AnyGraphNode,
) -> None:
    """Bind the error outs ``outputs`` of a node whose work can produce an error
    of its own to ``first_error(incoming, fragment.own_error)``, assigned after
    its code."""
    assert fragment.own_error is not None
    value = first_error(incoming, fragment.own_error)
    for term in outputs:
        var = ctx.make_output_var("error_out", node.id, term.id)
        fragment.statements.append(build_assign(var, parse_expr(value)))
        fragment.bindings[term.id] = var


def merge_errors(node: AnyGraphNode, ctx: CodeGenContext) -> CodeFragment:
    """Merge Errors: its error out is the first of its wired inputs, in input
    order, on which an error occurred; else the first warning; else ``None``."""
    errors = [
        value
        for term in error_terminals(node, "input")
        if (value := ctx.wired_input(term.id, node)) not in (None, "None")
    ]
    if not errors:
        merged = "None"
    else:
        candidates = f"({', '.join(errors)},)"
        merged = (
            f"next((e for e in {candidates} if {error_occurred('e')}), "
            f"next((e for e in {candidates} if e is not None), None))"
        )
    var = ctx.make_output_var("merged_error", node.id)
    bindings = {term.id: var for term in error_terminals(node, "output")}
    return CodeFragment(
        statements=[build_assign(var, parse_expr(merged))],
        bindings=bindings,
        runs_on_error=True,
    )


def _skipped_on_error(
    node: AnyGraphNode, fragment: CodeFragment, error: str, ctx: CodeGenContext
) -> CodeFragment:
    """The fragment's statements inside the guard on ``error``. An output the
    node computes as an expression is assigned to a variable inside the guard,
    so it is not computed when the node skips; an output assigned inside the
    guard takes its type's default when it skips, an error out the incoming
    error."""
    body = list(fragment.statements)
    assigned = assigned_names(body)
    own_outs = {t.id for t in own_error_terminals(node, ctx)[1]}
    for term in node.terminals:
        value = fragment.bindings.get(term.id)
        if term.direction != "output" or term.id in own_outs or value is None:
            continue
        expr = parse_expr(value)
        if not isinstance(expr, ast.Constant | ast.Name):
            var = ctx.make_output_var(
                to_var_name(term.name or "value"), node.id, term.id
            )
            body.append(build_assign(var, expr))
            fragment.bindings[term.id] = var
            assigned.add(var)
    if not body:
        return fragment
    # A skipped node passes the incoming error on.
    defaults = output_defaults(node, ctx, fragment.bindings, assigned, error)
    test = ast.If(
        test=parse_expr(no_error_occurred(error)),
        body=body,
        orelse=defaults,
    )
    setattr(test, _GUARD, error)
    fragment.statements = [test]
    return fragment


def output_defaults(
    node: AnyGraphNode,
    ctx: CodeGenContext,
    bindings: dict[str, str],
    assigned: set[str],
    error: str | None,
    values: Mapping[int, str] | None = None,
) -> list[ast.stmt]:
    """The assignments that give the outputs of ``node`` their value when its
    work did not happen (it was skipped, or it failed), in terminal order: each
    output bound to a variable in ``assigned`` takes the expression ``values``
    gives for its terminal index, else its type's default, and the node's own
    error out (``own_error_terminals``) takes ``error`` (left alone when
    ``error`` is ``None``)."""
    own_outs = {t.id for t in own_error_terminals(node, ctx)[1]}
    defaults: dict[str, ast.stmt] = {}
    for term in node.terminals:
        value = bindings.get(term.id)
        if term.direction != "output" or value is None or value not in assigned:
            continue
        if term.id not in own_outs:
            given = (values or {}).get(term.index)
            expr = (
                parse_expr(given)
                if given is not None
                else type_default(ctx, term.lv_type)
            )
            defaults.setdefault(value, build_assign(value, expr))
        elif error is not None:
            defaults.setdefault(value, build_assign(value, parse_expr(error)))
    return list(defaults.values())


def share_guards(statements: list[ast.stmt]) -> list[ast.stmt]:
    """``statements`` with each guard that follows a guard on the same error
    joined to it: consecutive nodes on one error chain run in one ``if``. The
    guards join only when the first does not assign a name the error is read
    from."""
    shared: list[ast.stmt] = []
    for stmt in statements:
        previous = shared[-1] if shared else None
        error = getattr(stmt, _GUARD, None)
        if (
            error is not None
            and isinstance(previous, ast.If)
            and isinstance(stmt, ast.If)
            and getattr(previous, _GUARD, None) == error
            and not _loaded_names(parse_expr(error)) & assigned_names(previous.body)
        ):
            previous.body.extend(stmt.body)
            previous.orelse.extend(stmt.orelse)
            continue
        shared.append(stmt)
    return shared


def _loaded_names(expr: ast.expr) -> set[str]:
    """The names an expression reads."""
    return {node.id for node in ast.walk(expr) if isinstance(node, ast.Name)}


def assigned_names(statements: list[ast.stmt]) -> set[str]:
    """The names the statements assign, at any depth."""
    names: set[str] = set()
    for stmt in statements:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
    return names
