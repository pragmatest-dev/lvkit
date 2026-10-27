"""Exception handling for the nodes that handle an error wire.

An error cluster is not a value in generated code: a failure raises and the
nodes that handle one catch it. Each handler wraps the statements that can raise
the error it handles (the nodes upstream of it on its error wire, see
``error_path``):

- Clear Errors: ``try: <producers> except LabVIEWError: pass``.
- Error case structure with an error frame that does work: ``try: <producers>``
  / ``except LabVIEWError as e: <error frame>; raise`` / ``else: <no-error
  frame>``.
- Merge Errors: the held error model (``error_handler``).

A handler whose error wire starts outside the diagram it sits in has nothing to
wrap: an error from the caller has already been raised. ``LabVIEWError`` is the
class of the package's generated ``errors.py``.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from lvkit.graph.models import AnyGraphNode, CaseStructureNode

from .context import CodeGenContext
from .error_path import ErrorRole, error_inputs, trace_error_path
from .errors import CodeGenError
from .errors_module import LABVIEW_ERROR_NAME
from .nodes import case

# Statements of a body, each tagged with the ids of the nodes that produced it.
Tagged = list[tuple[set[str], ast.stmt]]


def generate_error_handler(
    op: AnyGraphNode,
    role: ErrorRole,
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    tagged: Tagged,
) -> bool:
    """Generate ``op``, a node that handles an error wire, into ``tagged``.

    Returns False when ``op`` is generated like any other node: nothing in
    ``operations`` produces the error it handles.
    """
    handlers: dict[ErrorRole, Callable[..., bool]] = {
        ErrorRole.CLEAR: _clear_errors,
        ErrorRole.CASE: _error_case,
    }
    handler = handlers.get(role)
    if handler is None:
        return False
    return handler(op, operations, ctx, tagged)


def _handler(error_var: str | None, body: list[ast.stmt]) -> ast.ExceptHandler:
    return ast.ExceptHandler(
        type=ast.Name(id=LABVIEW_ERROR_NAME, ctx=ast.Load()),
        name=error_var,
        body=body or [ast.Pass()],
    )


def _try(
    body: list[ast.stmt],
    handler: ast.ExceptHandler,
    orelse: list[ast.stmt] | None = None,
) -> ast.Try:
    return ast.Try(body=body, handlers=[handler], orelse=orelse or [], finalbody=[])


def _clear_errors(
    op: AnyGraphNode,
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    tagged: Tagged,
) -> bool:
    """Clear Errors: the errors raised by the producers on its error wire are
    caught and dropped. Each run of consecutive producer statements gets its own
    ``try`` at its place, so a statement that reads a wrapped statement's value
    still runs after it."""
    if any(
        t.direction == "input" and not t.is_error_cluster and ctx.is_wired(t.id)
        for t in op.terminals
    ):
        raise CodeGenError(
            "Clear Errors with a specific error code is not generated yet", op
        )
    path = trace_error_path(error_inputs(op), operations, ctx)
    if not any(ids & path.op_ids for ids, _ in tagged):
        return False
    ctx.import_labview_error()

    def wrap(run: Tagged) -> tuple[set[str], ast.stmt]:
        ids = {op.id}
        for node_ids, _ in run:
            ids |= node_ids
        return ids, _try([s for _, s in run], _handler(None, []))

    result: Tagged = []
    run: Tagged = []
    for entry in tagged:
        if entry[0] & path.op_ids:
            run.append(entry)
            continue
        if run:
            result.append(wrap(run))
            run = []
        result.append(entry)
    if run:
        result.append(wrap(run))
    tagged[:] = result
    return True


def _error_case(
    op: AnyGraphNode,
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    tagged: Tagged,
) -> bool:
    """An error case structure whose error frame does work: the error frame is
    the handler of the producers, ending by raising the error again, and the
    no-error frame the ``else`` of the ``try``."""
    assert isinstance(op, CaseStructureNode)
    if not case.error_frame_ops(op, ctx):
        return False
    path = trace_error_path(error_inputs(op), operations, ctx)
    hits = [i for i, (ids, _) in enumerate(tagged) if ids & path.op_ids]
    if not hits:
        return False
    first, last = hits[0], hits[-1]
    ids: set[str] = {op.id}
    for node_ids, _ in tagged[first : last + 1]:
        ids |= node_ids
    ctx.import_labview_error()
    error_var = ctx.make_output_var("e", op.id)
    parts = case.generate_error_case_parts(op, ctx, error_var)
    ctx.merge(parts.bindings)
    ctx.imports.update(parts.imports)
    producers = [s for _, s in tagged[first : last + 1]]
    handler = _handler(error_var, [*parts.on_error, ast.Raise(exc=None, cause=None)])
    statements = [*parts.pre_decls, _try(producers, handler, parts.no_error)]
    tagged[first : last + 1] = [(ids, s) for s in statements]
    return True
