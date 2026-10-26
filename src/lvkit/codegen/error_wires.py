"""Exception handling for the nodes that handle an error wire.

An error cluster is not a value in generated code: a failure raises and the
nodes that handle one catch it. Each handler wraps the statements that can raise
the error it handles (the nodes upstream of it on its error wire, see
``error_path``):

- Clear Errors: ``try: <producers> except LabVIEWError: pass``.
- Simple / General Error Handler: the same, logging the error.
- Unbundle of an error cluster whose status / code / source something reads:
  ``try: <producers>; status = False`` / ``except LabVIEWError as e:
  status = True``.
- Error case structure with an error frame that does work: ``try: <producers>``
  / ``except LabVIEWError as e: <error frame>`` / ``else: <no-error frame>``.
- Merge Errors: nothing, the first exception propagates.

A handler whose error wire starts outside the diagram it sits in has nothing to
wrap: an error from the caller has already been raised.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from lvkit.graph.models import (
    AnyGraphNode,
    CaseStructureNode,
    PrimitiveNode,
)

from .ast_utils import build_assign, parse_expr
from .context import CodeGenContext
from .error_path import (
    ErrorRole,
    error_inputs,
    outputs_at_default,
    trace_error_path,
)
from .errors import CodeGenError
from .errors_module import LABVIEW_ERROR_NAME
from .nodes import case, nmux

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
        ErrorRole.HANDLER: _log_errors,
        ErrorRole.READ: _read_error_fields,
        ErrorRole.CASE: _error_case,
    }
    handler = handlers.get(role)
    if handler is None:
        return False
    return handler(op, operations, ctx, tagged)


def _producers(
    op: AnyGraphNode,
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    tagged: Tagged,
) -> tuple[int, int, set[str]] | None:
    """The first and last statement in ``tagged`` that can raise the error
    ``op`` handles, and the ids of the nodes behind the statements between."""
    path = trace_error_path(error_inputs(op), operations, ctx)
    hits = [i for i, (ids, _) in enumerate(tagged) if ids & path.op_ids]
    if not hits:
        return None
    first, last = hits[0], hits[-1]
    ids: set[str] = set()
    for node_ids, _ in tagged[first : last + 1]:
        ids |= node_ids
    return first, last, ids


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
    caught and dropped (only the one code wired to ``specific error code`` when
    there is one). Each run of consecutive producer statements gets its own
    ``try`` at its place, so a statement that reads a wrapped statement's value
    still runs after it."""
    path = trace_error_path(error_inputs(op), operations, ctx)
    if not any(ids & path.op_ids for ids, _ in tagged):
        return False
    ctx.import_labview_error()

    specific = [
        ctx.resolve(t.id)
        for t in op.terminals
        if t.direction == "input" and not t.is_error_cluster and ctx.is_wired(t.id)
    ]
    specific_code = specific[0] if specific and specific[0] != "0" else None

    def wrap(run: list[tuple[set[str], ast.stmt]]) -> tuple[set[str], ast.stmt]:
        error_var = ctx.make_output_var("e", op.id) if specific_code else None
        body: list[ast.stmt] = []
        if specific_code:
            body = [
                ast.If(
                    test=parse_expr(f"{error_var}.code != {specific_code}"),
                    body=[ast.Raise(exc=None, cause=None)],
                    orelse=[],
                )
            ]
        ids = {op.id}
        for node_ids, _ in run:
            ids |= node_ids
        return ids, _try([s for _, s in run], _handler(error_var, body))

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


def _log_errors(
    op: AnyGraphNode,
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    tagged: Tagged,
) -> bool:
    """Simple / General Error Handler: the error is logged and the VI goes on."""
    shown = [t for t in op.terminals if t.direction == "output" and ctx.is_wired(t.id)]
    wired_data = [t for t in shown if not t.is_error_cluster]
    if wired_data:
        raise CodeGenError(
            "The outputs of an error handler VI (error?, code out, source out, "
            "message) are not generated yet",
            op,
        )
    span = _producers(op, operations, ctx, tagged)
    if span is None:
        return False
    first, last, ids = span
    ctx.import_labview_error()
    ctx.add_import("import logging")
    error_var = ctx.make_output_var("e", op.id)
    log = ast.Expr(value=parse_expr(f'logging.error("%s", {error_var})'))
    tagged[first : last + 1] = [
        (
            ids | {op.id},
            _try([s for _, s in tagged[first : last + 1]], _handler(error_var, [log])),
        )
    ]
    return True


def _read_error_fields(
    op: AnyGraphNode,
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    tagged: Tagged,
) -> bool:
    """An Unbundle of an error cluster whose status / code / source is read:
    the producers run in a ``try``; each field is its no-error value after them
    and the caught error's value in the handler."""
    assert isinstance(op, PrimitiveNode)
    span = _producers(op, operations, ctx, tagged)
    if span is None:
        return False
    first, last, ids = span
    ctx.import_labview_error()
    error_var = ctx.make_output_var("e", op.id)
    ok: list[ast.stmt] = []
    caught: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    for read in nmux.error_unbundle_reads(op, ctx):
        var = ctx.make_output_var(read.role.value, op.id, read.terminal_id)
        ok.append(build_assign(var, parse_expr(read.no_error_value)))
        caught.append(build_assign(var, parse_expr(read.caught_value(error_var))))
        bindings[read.terminal_id] = var
    ctx.merge(bindings)
    body = [s for _, s in tagged[first : last + 1]] + ok
    tagged[first : last + 1] = [
        (ids | {op.id}, _try(body, _handler(error_var, caught)))
    ]
    return True


def _error_case(
    op: AnyGraphNode,
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    tagged: Tagged,
) -> bool:
    """An error case structure whose error frame does work: the error frame is
    the handler of the producers and the no-error frame the ``else`` of the
    ``try``. An error frame that leaves the error on the wire raises it again."""
    assert isinstance(op, CaseStructureNode)
    if not case.error_frame_ops(op, ctx):
        return False
    span = _producers(op, operations, ctx, tagged)
    if span is None:
        return False
    first, last, ids = span
    ctx.import_labview_error()
    error_var = ctx.make_output_var("e", op.id)
    producers_ops = [n for n in operations if n.id in ids]
    parts = case.generate_error_case_parts(
        op, ctx, error_var, lambda: outputs_at_default(producers_ops, ctx)
    )
    ctx.merge(parts.bindings)
    ctx.imports.update(parts.imports)
    on_error = list(parts.on_error)
    if parts.keeps_error:
        on_error.append(ast.Raise(exc=None, cause=None))
    producers = [s for _, s in tagged[first : last + 1]]
    statements = [
        *parts.pre_decls,
        _try(producers, _handler(error_var, on_error), parts.no_error),
    ]
    tagged[first : last + 1] = [(ids | {op.id}, s) for s in statements]
    return True
