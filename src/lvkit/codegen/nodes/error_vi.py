"""Code generator for the vi.lib error VIs a call site writes itself.

An error wire is a value (``error_flow``): Merge Errors merges its inputs, Clear
Errors sets its error out to ``None`` (or clears one code), Error Cluster From
Error Code builds a ``LabVIEWError`` from its code, and Simple Error Handler reports
the error in, or an error of its error code, without a dialog.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import VINode
from lvkit.models import TerminalDirection
from lvkit.parser.constants import (
    VI_NAME_CLEAR_ERRORS,
    VI_NAME_ERROR_CLUSTER_FROM_ERROR_CODE,
    VI_NAME_MERGE_ERRORS,
    VI_NAME_SIMPLE_ERROR_HANDLER,
)
from lvkit.vilib_resolver import VIEntry, get_resolver

from ..ast_utils import build_assign, parse_expr
from ..context import CodeGenContext
from ..error_codes import describe_error_code
from ..error_flow import (
    ErrorFieldRole,
    error_occurred,
    error_terminals,
    merge_errors,
)
from ..errors_module import LABVIEW_ERROR_NAME
from ..fragment import CodeFragment

_ERROR_CODE_TERMINALS = {
    "code": "error_code_0",
    "message": "error_message",
    "is_warning": "is_warning_false",
}


def generate(node: VINode, ctx: CodeGenContext) -> CodeFragment:
    """Generate the call of a vi.lib error VI (``error_path.is_error_wire_vi``)."""
    file_name = get_resolver().catalog_file_name(ctx.catalog_path(node))
    if file_name == VI_NAME_MERGE_ERRORS:
        return merge_errors(node, ctx)
    if file_name == VI_NAME_CLEAR_ERRORS:
        return _clear_errors(node, ctx)
    if file_name == VI_NAME_ERROR_CLUSTER_FROM_ERROR_CODE:
        return _error_cluster_from_error_code(node, ctx)
    if file_name == VI_NAME_SIMPLE_ERROR_HANDLER:
        return _simple_error_handler(node, ctx)
    return CodeFragment.empty()


def _incoming_error(node: VINode, ctx: CodeGenContext) -> str:
    """The error that comes in on the node's error in; ``None`` when unwired."""
    error_in = next(iter(error_terminals(node, "input")), None)
    return (ctx.wired_input(error_in.id, node) if error_in else None) or "None"


def _clear_errors(node: VINode, ctx: CodeGenContext) -> CodeFragment:
    """Clear Errors: its error out is ``None``. With a specific error code wired
    (not 0), only an error of that code is cleared, and ``specific error
    cleared?`` says whether it was. It runs whatever error comes in."""
    terminals = ctx.enriched_terminals(node)
    incoming = _incoming_error(node, ctx)
    code_in = next(
        (t for t in terminals if t.direction == "input" and not t.is_error_cluster),
        None,
    )
    code = ctx.wired_input(code_in.id, node) if code_in is not None else None
    statements: list[ast.stmt] = []
    if code is None or code == "0":
        cleared, error = "False", "None"
    else:
        cleared = ctx.make_output_var("specific_error_cleared", node.id)
        error = ctx.make_output_var("cleared_error", node.id)
        statements = [
            build_assign(
                cleared,
                parse_expr(f"{incoming} is not None and {incoming}.code == {code}"),
            ),
            build_assign(error, parse_expr(f"None if {cleared} else {incoming}")),
        ]
    bindings = {
        term.id: error if term.is_error_cluster else cleared
        for term in terminals
        if term.direction == "output"
    }
    return CodeFragment(statements=statements, bindings=bindings, runs_on_error=True)


def _error_cluster_from_error_code(node: VINode, ctx: CodeGenContext) -> CodeFragment:
    """Error Cluster From Error Code: a ``LabVIEWError`` of the code (a warning
    when ``is warning?`` is True), or the error that came in for the code 0. A
    constant code is known here, so its description is written into the module
    as the message (the wired message wins when there is one). The node is
    skipped, passing the incoming error on, when an error comes in
    (``error_flow``)."""
    entry = get_resolver().resolve_recorded(ctx.catalog_path(node))
    if entry is None:
        return CodeFragment.empty()

    def wired(role: str) -> str | None:
        return _catalog_input(node, ctx, entry, _ERROR_CODE_TERMINALS[role])

    code = wired("code")
    if code is None:
        return CodeFragment.empty()
    constant = _int_constant(code)
    if constant == 0:
        return CodeFragment.empty()

    message = wired("message")
    if message in ("''", '""'):
        message = None
    description = describe_error_code(constant) if isinstance(constant, int) else None
    if description is not None:
        message = f"{message} or {description!r}" if message else repr(description)
    warning = wired("is_warning")
    status = {None: "True", "False": "True", "True": "False"}.get(
        warning, f"not {warning}"
    )
    error = (
        f"{LABVIEW_ERROR_NAME}(code={code}, message={message or repr('')}, "
        f"status={status})"
    )
    ctx.import_labview_error()
    if not isinstance(constant, int):
        error = f"{error} if {code} != 0 else {_incoming_error(node, ctx)}"
    var = ctx.make_output_var("error", node.id)
    return CodeFragment(
        statements=[build_assign(var, parse_expr(error))],
        bindings={term.id: var for term in error_terminals(node, "output")},
    )


_SIMPLE_ERROR_HANDLER_OUTPUTS = {
    "error": ErrorFieldRole.STATUS,
    "code_out": ErrorFieldRole.CODE,
    "source_out": ErrorFieldRole.SOURCE,
}

# The inputs of Simple Error Handler the error it reports is made from.
_HANDLER_CODE = "error_code_no_error_0"
_HANDLER_SOURCE = "error_source"


def _simple_error_handler(node: VINode, ctx: CodeGenContext) -> CodeFragment:
    """Simple Error Handler: the error it reports is the error in when an error
    occurred on it; else, for a nonzero ``error code``, an error of that code
    from ``error source``; else the error in (``None``, or a warning). Its error
    out is that error, ``error?``, ``code out`` and ``source out`` are its
    status, code and source, and ``message`` its message. It shows no dialog.
    It runs whatever error comes in."""
    entry = get_resolver().resolve_recorded(ctx.catalog_path(node))
    if entry is None:
        return CodeFragment.empty()
    param_of = {
        t.index: t.python_param
        for t in entry.terminals
        if t.direction == TerminalDirection.OUTPUT
    }
    statements: list[ast.stmt] = []
    error = _reported_error(node, ctx, entry)
    if error != _incoming_error(node, ctx):
        var = ctx.make_output_var("handled_error", node.id)
        statements.append(build_assign(var, parse_expr(error)))
        error = var
    message = "''" if error == "None" else f"{error}.message if {error} else ''"
    bindings = {term.id: error for term in error_terminals(node, "output")}
    for term in ctx.enriched_terminals(node):
        param = param_of.get(term.index)
        if term.direction != "output" or term.is_error_cluster:
            continue
        role = _SIMPLE_ERROR_HANDLER_OUTPUTS.get(param or "")
        if role is None and param != "message":
            continue
        if not ctx.is_wired(term.id):
            continue
        var = ctx.make_output_var(param or "value", node.id, term.id)
        value = message if role is None else role.in_error(error)
        statements.append(build_assign(var, parse_expr(value)))
        bindings[term.id] = var
    return CodeFragment(statements=statements, bindings=bindings, runs_on_error=True)


def _reported_error(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> str:
    """The error Simple Error Handler reports, as an expression: the error in
    when an error occurred on it; else an error of the ``error code`` input with
    the ``error source`` input, when the code is not 0; else the error in. An
    unwired ``error code`` is 0, so the error in is all there is."""
    incoming = _incoming_error(node, ctx)
    code = _catalog_input(node, ctx, entry, _HANDLER_CODE)
    constant = _int_constant(code) if code is not None else 0
    if code is None or constant == 0:
        return incoming
    source = _catalog_input(node, ctx, entry, _HANDLER_SOURCE)
    if source is None:
        source = repr(_catalog_default(entry, _HANDLER_SOURCE) or "")
    description = describe_error_code(constant) if constant is not None else None
    ctx.import_labview_error()
    found = (
        f"{LABVIEW_ERROR_NAME}(code={code}, source={source}, "
        f"message={description or ''!r}, status=True)"
    )
    if constant is None:
        found = f"{found} if {code} != 0 else {incoming}"
    if incoming == "None":
        return found
    return f"{incoming} if {error_occurred(incoming)} else ({found})"


def _catalog_input(
    node: VINode, ctx: CodeGenContext, entry: VIEntry, param: str
) -> str | None:
    """The expression wired to the input the catalog entry names ``param``;
    ``None`` when it is unwired."""
    index = next(
        (
            t.index
            for t in entry.terminals
            if t.direction == TerminalDirection.INPUT and t.python_param == param
        ),
        None,
    )
    for term in ctx.enriched_terminals(node):
        if term.direction == "input" and term.index == index:
            return ctx.wired_input(term.id, node)
    return None


def _catalog_default(entry: VIEntry, param: str) -> object:
    """The default the catalog entry records for its input ``param``."""
    return next((t.default for t in entry.terminals if t.python_param == param), None)


def _int_constant(expression: str) -> int | None:
    """The integer ``expression`` is when it is a constant, else ``None``."""
    try:
        value = ast.literal_eval(expression)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None
