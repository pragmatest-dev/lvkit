"""Which nodes run when an error comes in on their error in.

An error wire is a value: ``None`` when no error occurred, else a
``LabVIEWError`` whose ``status`` says whether it is an error (``True``) or a
warning (``False``). A node on an error chain (an error in and an error out)
skips its work when an error occurred, as LabVIEW's nodes do, and passes the
incoming error on to its error out; a warning does not skip it. So the node's
statements run inside ``if err is None or not err.status:``, its outputs take
their type's default when it skips (``ast_utils.type_default_expr``), and an
error out the node does not set itself is its error in.

A node is not skipped by its caller when its own code reads the error in: a
subVI or method call passes it to the called function, which decides
(``CodeFragment.runs_on_error``). A structure is never skipped as a whole; the
nodes inside it are. Consecutive nodes on one error chain share one ``if``
(``share_guards``). Merge Errors combines its inputs (``merge_errors``).
"""

from __future__ import annotations

import ast
from enum import Enum
from typing import TYPE_CHECKING

from lvkit.graph.models import AnyGraphNode, StructureNode
from lvkit.models import Terminal

from .ast_utils import build_assign, parse_expr, to_var_name, type_default_expr
from .fragment import CodeFragment

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


def guard(
    node: AnyGraphNode, fragment: CodeFragment, ctx: CodeGenContext
) -> CodeFragment:
    """``fragment``, the code of ``node``, run only when no error came in on the
    node's error in, and its error outputs that the code does not set bound to
    the incoming error.

    The error in of a node is its one error cluster input, on a node that also
    has an error cluster output. A node with several error inputs (Merge Errors)
    combines them in its own code."""
    if isinstance(node, StructureNode):
        return fragment
    inputs = error_terminals(node, "input")
    outputs = error_terminals(node, "output")
    incoming = (
        ctx.wired_input(inputs[0].id, node) if len(inputs) == 1 and outputs else None
    )
    error = incoming or "None"
    for term in outputs:
        fragment.bindings.setdefault(term.id, error)
    if incoming is None or incoming == "None" or fragment.runs_on_error:
        return fragment
    return _skipped_on_error(node, fragment, incoming, ctx)


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
    assigned = _assigned_names(body)
    defaults: dict[str, ast.stmt] = {}
    for term in node.terminals:
        if term.direction != "output":
            continue
        value = fragment.bindings.get(term.id)
        if term.is_error_cluster:
            # A skipped node passes the incoming error on.
            if value is not None and value.isidentifier() and value in assigned:
                defaults.setdefault(value, build_assign(value, parse_expr(error)))
            continue
        if value is None:
            continue
        expr = parse_expr(value)
        if isinstance(expr, ast.Constant) or (
            isinstance(expr, ast.Name) and expr.id not in assigned
        ):
            continue
        if not isinstance(expr, ast.Name):
            var = ctx.make_output_var(
                to_var_name(term.name or "value"), node.id, term.id
            )
            body.append(build_assign(var, expr))
            fragment.bindings[term.id] = var
            value = var
        defaults.setdefault(value, build_assign(value, type_default_expr(term.lv_type)))
    if not body:
        return fragment
    test = ast.If(
        test=parse_expr(no_error_occurred(error)),
        body=body,
        orelse=list(defaults.values()),
    )
    setattr(test, _GUARD, error)
    fragment.statements = [test]
    return fragment


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
            and not _loaded_names(parse_expr(error)) & _assigned_names(previous.body)
        ):
            previous.body.extend(stmt.body)
            previous.orelse.extend(stmt.orelse)
            continue
        shared.append(stmt)
    return shared


def _loaded_names(expr: ast.expr) -> set[str]:
    """The names an expression reads."""
    return {node.id for node in ast.walk(expr) if isinstance(node, ast.Name)}


def _assigned_names(statements: list[ast.stmt]) -> set[str]:
    """The names the statements assign, at any depth."""
    names: set[str] = set()
    for stmt in statements:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
    return names
