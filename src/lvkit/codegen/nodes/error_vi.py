"""Code generator for the vi.lib error VIs a call site writes itself.

An error wire is a value (``error_flow``): Merge Errors merges its inputs, Clear
Errors sets its error out to ``None`` (or clears one code), Error Cluster From
Error Code builds a ``LabVIEWError`` from its code, and Simple and General Error
Handler report the error in, or an error of their error code, without a dialog.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import TerminalDirection
from lvkit.parser.constants import (
    VI_NAME_CLEAR_ERRORS,
    VI_NAME_ERROR_CLUSTER_FROM_ERROR_CODE,
    VI_NAME_GENERAL_ERROR_HANDLER,
    VI_NAME_MERGE_ERRORS,
    VI_NAME_SIMPLE_ERROR_HANDLER,
)
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import build_assign, paren_if_compound, parse_expr
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


# The input of Clear Errors that names the one code to clear.
_CLEAR_CODE = "specific_error_code_to_clear_0_all"


def generate(node: VINode, ctx: CodeGenContext) -> CodeFragment:
    """Generate the call of a vi.lib error VI (``error_path.is_error_wire_vi``)."""
    file_name = ctx.vilib_catalog.catalog_file_name(ctx.catalog_path(node))
    if file_name == VI_NAME_MERGE_ERRORS:
        return merge_errors(node, ctx)
    if file_name == VI_NAME_CLEAR_ERRORS:
        return _clear_errors(node, ctx)
    if file_name == VI_NAME_ERROR_CLUSTER_FROM_ERROR_CODE:
        return _error_cluster_from_error_code(node, ctx)
    if file_name in (VI_NAME_SIMPLE_ERROR_HANDLER, VI_NAME_GENERAL_ERROR_HANDLER):
        return _error_handler(node, ctx)
    return CodeFragment.empty()


def _incoming_error(node: VINode, ctx: CodeGenContext) -> str:
    """The error that comes in on the node's error in; ``None`` when unwired."""
    error_in = next(iter(error_terminals(node, "input")), None)
    return (ctx.wired_input(error_in.id, node) if error_in else None) or "None"


def _clear_errors(node: VINode, ctx: CodeGenContext) -> CodeFragment:
    """Clear Errors: its error out is ``None``. With a specific error code wired,
    only an error of that code is cleared (the code 0 clears every error), and
    ``specific error cleared?`` says whether it was. It runs whatever error comes
    in."""
    entry = ctx.vilib_catalog.resolve_recorded(ctx.catalog_path(node))
    if entry is None:
        return CodeFragment.empty()
    terminals = ctx.enriched_terminals(node)
    incoming = _incoming_error(node, ctx)
    code = _catalog_input(node, ctx, entry, _CLEAR_CODE)
    statements: list[ast.stmt] = []
    if code is None or code == "0":
        cleared, error = "False", "None"
    else:
        cleared = ctx.make_output_var("specific_error_cleared", node.id)
        error = ctx.make_output_var("cleared_error", node.id)
        matches = f"{incoming}.code == {code}"
        if _int_constant(code) is None:
            code_var = ctx.make_output_var("specific_error_code", node.id)
            statements.append(build_assign(code_var, parse_expr(code)))
            matches = f"{code_var} == 0 or {incoming}.code == {code_var}"
        test = f"{incoming} is not None and ({matches})"
        statements += [
            build_assign(cleared, parse_expr(test)),
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
    entry = ctx.vilib_catalog.resolve_recorded(ctx.catalog_path(node))
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
        message = (
            f"{paren_if_compound(message)} or {description!r}"
            if message
            else repr(description)
        )
    warning = wired("is_warning")
    if warning in (None, "False"):
        status = "True"
    elif warning == "True":
        status = "False"
    else:
        status = f"not {paren_if_compound(warning)}"
    error = (
        f"{LABVIEW_ERROR_NAME}(code={code}, message={message or repr('')}, "
        f"status={status})"
    )
    ctx.import_labview_error()
    if not isinstance(constant, int):
        error = (
            f"{error} if {paren_if_compound(code)} != 0 "
            f"else {_incoming_error(node, ctx)}"
        )
    var = ctx.make_output_var("error", node.id)
    return CodeFragment(
        statements=[build_assign(var, parse_expr(error))],
        bindings={term.id: var for term in error_terminals(node, "output")},
    )


_ERROR_HANDLER_OUTPUTS = {
    "error": ErrorFieldRole.STATUS,
    "code_out": ErrorFieldRole.CODE,
    "source_out": ErrorFieldRole.SOURCE,
}

# The inputs of Simple and General Error Handler the error they report is made
# from (a catalog entry names the input by its own label).
_HANDLER_CODE = ("error_code_no_error_0", "error_code")
_HANDLER_SOURCE = ("error_source",)

# The inputs only General Error Handler has.
_USER_CODES = ("user_defined_codes",)
_USER_DESCRIPTIONS = ("user_defined_descriptions",)
_EXCEPTION_ACTION = ("exception_action",)
_EXCEPTION_CODE = ("exception_code",)
_EXCEPTION_SOURCE = ("exception_source",)

# NI's values of General Error Handler's exception action.
_NO_EXCEPTION, _CANCEL_ON_MATCH, _SET_ON_MATCH = 0, 1, 2


def _error_handler(node: VINode, ctx: CodeGenContext) -> CodeFragment:
    """Simple and General Error Handler: the error they report is the error in
    when an error occurred on it; else, for a nonzero ``error code``, an error of
    that code from ``error source``; else the error in (``None``, or a warning).
    Their error out is that error, ``error?``, ``code out`` and ``source out``
    are its status, code and source, and ``message`` its message. They show no
    dialog. They run whatever error comes in. General Error Handler's exception
    action, and the user-defined codes with their descriptions, change the error
    and the message (``_with_exception``, ``_message``)."""
    entry = ctx.vilib_catalog.resolve_recorded(ctx.catalog_path(node))
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
    excepted = _with_exception(node, ctx, entry, error)
    if excepted != error:
        var = ctx.make_output_var("excepted_error", node.id)
        statements.append(build_assign(var, parse_expr(excepted)))
        error = var
    message = _message(node, ctx, entry, error, statements)
    bindings = {term.id: error for term in error_terminals(node, "output")}
    for term in ctx.enriched_terminals(node):
        param = param_of.get(term.index)
        if term.direction != "output" or term.is_error_cluster:
            continue
        role = _ERROR_HANDLER_OUTPUTS.get(param or "")
        if role is None and param != "message":
            continue
        if not ctx.is_wired(term.id):
            continue
        var = ctx.make_output_var(param or "value", node.id, term.id)
        value = message if role is None else role.in_error(error)
        statements.append(build_assign(var, parse_expr(value)))
        bindings[term.id] = var
    return CodeFragment(statements=statements, bindings=bindings, runs_on_error=True)


def _with_exception(
    node: VINode, ctx: CodeGenContext, entry: VIEntry, error: str
) -> str:
    """The error General Error Handler reports after its exception action.

    NI: "The VI performs the exception action if the error code and error source
    match the exception code and exception source. If you use the default value
    for an exception source, only the exception code must match."
    "Cancel error on match: Treats what is normally an error as no error. If the
    VI cancels an error, error? is FALSE, code out is 0, and source out is an
    empty string." "Set error on match: Upgrades a warning to an error ... If the
    VI sets an error, error? is TRUE, code out is the code value from error in,
    and source out is the source value from error in."
    The action is read when the code is generated, so it must be a constant;
    ``error`` is a variable or ``None``, and a wire holding no error never
    matches."""
    action = _catalog_input(node, ctx, entry, *_EXCEPTION_ACTION)
    if action is None:
        return error
    constant = _int_constant(action)
    if constant not in (_NO_EXCEPTION, _CANCEL_ON_MATCH, _SET_ON_MATCH):
        raise ConversionGap(
            f"{node.name} 'exception action' is {action}, which is not a "
            "constant value NI documents (0 No exception, 1 Cancel error on "
            "match, 2 Set error on match), so which exception it performs is "
            "only known when the VI runs",
            node,
        )
    if constant == _NO_EXCEPTION or error == "None":
        return error
    code = _catalog_input(node, ctx, entry, *_EXCEPTION_CODE) or "0"
    matches = f"{error}.code == {paren_if_compound(code)}"
    source = _catalog_input(node, ctx, entry, *_EXCEPTION_SOURCE)
    if source is not None and source not in ("''", '""'):
        source = paren_if_compound(source)
        matches = f"{matches} and ({source} == '' or {error}.source == {source})"
    if constant == _CANCEL_ON_MATCH:
        return f"None if {error_occurred(error)} and {matches} else {error}"
    ctx.import_labview_error()
    upgraded = (
        f"{LABVIEW_ERROR_NAME}(status=True, code={error}.code, "
        f"source={error}.source, message={error}.message)"
    )
    return (
        f"{upgraded} if {error} is not None and not {error}.status and {matches} "
        f"else {error}"
    )


def _message(
    node: VINode,
    ctx: CodeGenContext,
    entry: VIEntry,
    error: str,
    statements: list[ast.stmt],
) -> str:
    """The ``message`` of the error ``error``. NI: "If an incoming error matches
    one in user-defined codes, the VI returns the corresponding description from
    user-defined descriptions in message." "The VI searches this array after
    searching an internal database of error codes", so the error's own message
    comes first, then the description of its code in the user-defined codes. The
    two arrays are wired together or the codes mean nothing."""
    own = "''" if error == "None" else f"{error}.message if {error} else ''"
    codes = _catalog_input(node, ctx, entry, *_USER_CODES)
    descriptions = _catalog_input(node, ctx, entry, *_USER_DESCRIPTIONS)
    if error == "None" or codes is None or descriptions is None:
        return own
    codes_var = ctx.make_output_var("user_defined_codes", node.id)
    descriptions_var = ctx.make_output_var("user_defined_descriptions", node.id)
    statements += [
        build_assign(codes_var, parse_expr(codes)),
        build_assign(descriptions_var, parse_expr(descriptions)),
    ]
    position = f"{codes_var}.index({error}.code)"
    return (
        f"{error}.message or ({descriptions_var}[{position}] "
        f"if {error}.code in {codes_var} and {position} < len({descriptions_var}) "
        "else '') if "
        f"{error} else ''"
    )


def _reported_error(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> str:
    """The error Simple and General Error Handler report, as an expression: the
    error in when an error occurred on it; else an error of the ``error code``
    input with the ``error source`` input, when the code is not 0; else the error
    in. An unwired ``error code`` is 0, so the error in is all there is."""
    incoming = _incoming_error(node, ctx)
    code = _catalog_input(node, ctx, entry, *_HANDLER_CODE)
    constant = _int_constant(code) if code is not None else 0
    if code is None or constant == 0:
        return incoming
    source = _catalog_input(node, ctx, entry, *_HANDLER_SOURCE)
    if source is None:
        source = repr(_catalog_default(entry, *_HANDLER_SOURCE) or "")
    description = describe_error_code(constant) if constant is not None else None
    ctx.import_labview_error()
    found = (
        f"{LABVIEW_ERROR_NAME}(code={code}, source={source}, "
        f"message={description or ''!r}, status=True)"
    )
    if constant is None:
        found = f"{found} if {paren_if_compound(code)} != 0 else {incoming}"
    if incoming == "None":
        return found
    return f"{incoming} if {error_occurred(incoming)} else ({found})"


def _catalog_input(
    node: VINode, ctx: CodeGenContext, entry: VIEntry, *params: str
) -> str | None:
    """The expression wired to the input the catalog entry names one of
    ``params``; ``None`` when it is unwired (or the entry has no such input)."""
    index = next(
        (
            t.index
            for t in entry.terminals
            if t.direction == TerminalDirection.INPUT and t.python_param in params
        ),
        None,
    )
    for term in ctx.enriched_terminals(node):
        if term.direction == "input" and term.index == index:
            return ctx.wired_input(term.id, node)
    return None


def _catalog_default(entry: VIEntry, *params: str) -> object:
    """The default the catalog entry records for its input named one of
    ``params``."""
    return next((t.default for t in entry.terminals if t.python_param in params), None)


def _int_constant(expression: str) -> int | None:
    """The integer ``expression`` is when it is a constant, else ``None``."""
    try:
        value = ast.literal_eval(expression)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None
