"""Exception handling for the nodes that handle an error wire.

An error cluster is not a value in generated code: a failure raises and the
nodes that handle one catch it. Each handler wraps the statements that can raise
the error it handles (the nodes upstream of it on its error wire, see
``error_path``):

- Clear Errors: ``try: <producers> except LabVIEWError: pass``.
- Error case structure with an error frame that does work: ``try: <producers>``
  / ``except LabVIEWError as e: <error frames>`` / ``else: <no-error frame>``.
  An error frame selected by an error code is an ``if`` on ``e.code``; the frame
  that covers the remaining errors is the ``else``. A frame ends with ``raise``
  only when it sends the error out of the case unchanged.
- Merge Errors: the held error model (``error_handler``).

A handler whose error wire starts outside the diagram it sits in has nothing to
wrap: an error from the caller has already been raised. ``LabVIEWError`` is the
class of the package's generated ``errors.py``.
"""

from __future__ import annotations

import ast
from collections.abc import Callable

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode, CaseStructureNode

from .ast_utils import parse_expr
from .context import CodeGenContext
from .error_handler import assigned_names, failed_output_defaults
from .error_path import ErrorRole, classify_error_node, error_inputs, trace_error_path
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


def _is_handler(op: AnyGraphNode, ctx: CodeGenContext) -> bool:
    """Whether ``op`` handles the error on its wire: a Clear Errors, or an error
    case structure with an error frame that does work."""
    role = classify_error_node(op)
    return role is ErrorRole.CLEAR or (
        role is ErrorRole.CASE
        and isinstance(op, CaseStructureNode)
        and bool(case.error_frame_ops(op, ctx))
    )


def handled_producer_ids(
    operations: list[AnyGraphNode], ctx: CodeGenContext
) -> set[str]:
    """The ids of the nodes among ``operations`` whose error a handler consumes:
    the nodes upstream of the error input of a Clear Errors or of an error case
    structure with an error frame that does work. The handler catches what they
    raise, so a parallel branch among them raises its error instead of holding
    it until the end."""
    ids: set[str] = set()
    for op in operations:
        if _is_handler(op, ctx):
            ids |= trace_error_path(error_inputs(op), operations, ctx).op_ids
    return ids


def merged_producer_ids(
    operations: list[AnyGraphNode], ctx: CodeGenContext
) -> set[str]:
    """The ids of the nodes among ``operations`` whose error reaches a Merge
    Errors without passing a handler: the merge raises it, so a parallel branch
    among them holds its error until the merge."""
    ids: set[str] = set()
    for op in operations:
        if classify_error_node(op) is ErrorRole.MERGE:
            path = trace_error_path(
                error_inputs(op), operations, ctx, lambda o: _is_handler(o, ctx)
            )
            ids |= path.op_ids
    return ids


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
        raise ConversionGap(
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


def _frame_statements(frame: case.ErrorFrameParts) -> list[ast.stmt]:
    """The statements of one error frame, raising the error again when the
    frame sends it out of the case unchanged."""
    body = list(frame.body)
    if frame.re_raises:
        body.append(ast.Raise(exc=None, cause=None))
    return body or [ast.Pass()]


def _error_frames(frames: list[case.ErrorFrameParts]) -> list[ast.stmt]:
    """The body of the handler: an ``if`` per frame selected by an error code, in
    frame order, ending in the frame that covers the remaining errors. When no
    frame covers them, they are raised again."""
    remaining = next((f for f in frames if f.condition is None), None)
    coded = [f for f in frames if f.condition is not None]
    if not coded and remaining is not None:
        return _frame_statements(remaining)
    chain: list[ast.stmt] = (
        _frame_statements(remaining)
        if remaining is not None
        else [ast.Raise(exc=None, cause=None)]
    )
    for frame in reversed(coded):
        assert frame.condition is not None
        chain = [
            ast.If(
                test=parse_expr(frame.condition),
                body=_frame_statements(frame),
                orelse=chain,
            )
        ]
    return chain


def _producer_outputs_read_by_error_frames(
    producer_ops: list[AnyGraphNode],
    producers: list[ast.stmt],
    parts: case.ErrorCaseParts,
    ctx: CodeGenContext,
) -> list[ast.stmt]:
    """A declaration, before the ``try``, of each variable the producers assign
    and an error frame reads: the producer that failed never assigned it. It
    holds the default of its type, as the output of a failed node does."""
    bindings = {
        term.id: value
        for op in producer_ops
        for term in op.terminals
        if term.direction == "output"
        and not term.is_error_cluster
        and (value := ctx.resolve(term.id))
    }
    read = {
        node.id
        for frame in parts.on_error
        for stmt in frame.body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    names = sorted(
        {
            root
            for value in bindings.values()
            if (root := value.split(".")[0]).isidentifier()
        }
        & assigned_names(producers)
        & read
    )
    defaults = failed_output_defaults(bindings, names, ctx)
    return [
        ast.Assign(targets=[ast.Name(id=name, ctx=ast.Store())], value=default)
        for name, default in defaults.items()
    ]


def _error_case(
    op: AnyGraphNode,
    operations: list[AnyGraphNode],
    ctx: CodeGenContext,
    tagged: Tagged,
) -> bool:
    """An error case structure whose error frames do work: the error frames are
    the handler of the producers, and the no-error frame the ``else`` of the
    ``try``."""
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
    handler = _handler(error_var, _error_frames(parts.on_error))
    declared = _producer_outputs_read_by_error_frames(
        [o for o in operations if o.id in path.op_ids], producers, parts, ctx
    )
    statements = [
        *parts.pre_decls,
        *declared,
        _try(producers, handler, parts.no_error),
    ]
    tagged[first : last + 1] = [(ids, s) for s in statements]
    return True
