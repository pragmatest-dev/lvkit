"""A catalog node's Python operation that fails gives the node's error out.

A catalog entry whose operation can fail lists how (``NativeError``): each
Python exception class it can raise and the LabVIEW error code that failure is,
from NI's public error-code list, or none for the generic code. The node's
statements run in a ``try``; each listed exception, in the listed order, is an
``except`` that gives the node's outputs their type's default (as a skipped
node's are) and its error out a ``LabVIEWError`` of that code, with the node's
name as its source and the exception's text (else NI's description of the
code) as its message. When nothing fails, the error out is the error that came
in. Only the listed exceptions are caught, so a gap or an lvkit bug in the
generated code is never turned into a LabVIEW error.

A node NI runs despite an incoming error (``CodeFragment.runs_on_error``)
reports its own failure only when no error came in: the failure is its
``own_error`` and ``error_flow.incoming_error_wins`` picks its error out.

A node whose error out is not wired has nowhere to put the error, so its
failure stays a Python exception.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from typing import TYPE_CHECKING

from lvkit.graph.models import AnyGraphNode
from lvkit.primitive_resolver import NativeError

from .ast_utils import build_assign, parse_expr
from .error_codes import describe_error_code
from .error_flow import assigned_names, error_terminals, output_defaults
from .errors_module import LABVIEW_ERROR_NAME
from .fragment import CodeFragment

if TYPE_CHECKING:
    from .context import CodeGenContext

# NI, General LabVIEW Error Codes: 42 "Generic error."
GENERIC_ERROR_CODE = 42


def catch_failures(
    node: AnyGraphNode,
    fragment: CodeFragment,
    failures: Sequence[NativeError],
    source: str,
    ctx: CodeGenContext,
) -> CodeFragment:
    """``fragment``, the code of the catalog node ``node`` named ``source``, with
    each of its ``failures`` caught and made its error out."""
    outputs = error_terminals(node, "output")
    if (
        not failures
        or not fragment.statements
        or not any(ctx.is_wired(term.id) for term in outputs)
    ):
        return fragment
    if fragment.runs_on_error:
        error = ctx.make_output_var("own_error", node.id)
        succeeded = "None"
        fragment.own_error = error
    else:
        error = ctx.make_output_var("error_out", node.id, outputs[0].id)
        succeeded = _incoming_error(node, ctx)
        for term in outputs:
            fragment.bindings[term.id] = error
    defaults = output_defaults(
        node, fragment.bindings, assigned_names(fragment.statements), None
    )
    exception = ctx.make_output_var("exception", node.id)
    handlers = []
    for failure in failures:
        if failure.module is not None:
            fragment.imports.add(f"from {failure.module} import {failure.exception}")
        handlers.append(
            ast.ExceptHandler(
                type=ast.Name(id=failure.exception, ctx=ast.Load()),
                name=exception,
                body=[
                    *defaults,
                    build_assign(error, _labview_error(failure, source, exception)),
                ],
            )
        )
    ctx.import_labview_error()
    fragment.statements = [
        ast.Try(
            body=fragment.statements,
            handlers=handlers,
            orelse=[build_assign(error, parse_expr(succeeded))],
            finalbody=[],
        )
    ]
    return fragment


def _labview_error(failure: NativeError, source: str, exception: str) -> ast.expr:
    """``LabVIEWError(code=…, source=…, message=…)`` for ``failure``, caught as
    the variable ``exception``."""
    code = GENERIC_ERROR_CODE if failure.code is None else failure.code
    message = f"str({exception})"
    description = describe_error_code(code)
    if description is not None:
        message = f"{message} or {description!r}"
    return parse_expr(
        f"{LABVIEW_ERROR_NAME}(code={code}, source={source!r}, message={message})"
    )


def _incoming_error(node: AnyGraphNode, ctx: CodeGenContext) -> str:
    """The error that comes in on the node's one error in; ``None`` when there
    is none or it is unwired."""
    inputs = error_terminals(node, "input")
    if len(inputs) != 1:
        return "None"
    return ctx.wired_input(inputs[0].id, node) or "None"
