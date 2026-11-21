"""A catalog node's Python operation that fails gives the node's error out.

A catalog entry whose operation can fail lists how (``NativeError``): each
Python exception class it can raise and the LabVIEW error code that failure is,
from NI's public error-code list, or none for the generic code. The node's
statements run in a ``try``; each listed exception, in the listed order, is an
``except`` that gives the node's outputs their type's default (as a skipped
node's are), or the value NI documents for an output on a failure (the catalog
terminal's ``on_failure``), and its error out a ``LabVIEWError`` of that code,
with the node's name as its source and the exception's text (else NI's
description of the code) as its message. When nothing fails, the error out is
the error that came in. Only the listed exceptions are caught, so a gap or an
lvkit bug in the generated code is never turned into a LabVIEW error.

A node NI runs despite an incoming error (``CodeFragment.runs_on_error``)
reports its own failure only when no error came in: the failure is its
``own_error`` and ``error_flow.incoming_error_wins`` picks its error out.

A node whose error out is not wired has nowhere to put the error, so the VI's
automatic error handling (VI Properties > Execution,
``ExecutionProps.auto_error_handling``) decides, as in LabVIEW. On, the failure
stays a Python exception, and a call of a generated VI raises the error the
callee returns (``raise_unwired_error``). Off, the failure is caught and
dropped: the node's outputs take the values they take on any failure and the
run continues.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lvkit.graph.models import AnyGraphNode
from lvkit.models import ScalarValue, TerminalDirection, contains_class_instance
from lvkit.primitive_resolver import NativeError, PrimitiveEntry
from lvkit.vilib_resolver import VIEntry

from .ast_utils import build_assign, parse_expr
from .error_codes import describe_error_code
from .error_flow import (
    assigned_names,
    error_occurred,
    output_defaults,
    own_error_terminals,
)
from .errors_module import LABVIEW_ERROR_NAME
from .fragment import CodeFragment
from .import_resolver import ImportResolver

if TYPE_CHECKING:
    from .context import CodeGenContext

# NI, General LabVIEW Error Codes: 42 "Generic error."
GENERIC_ERROR_CODE = 42


@dataclass(frozen=True)
class CatalogFailures:
    """What a catalog entry says about its operation's failures: ``failures``,
    caught in order; ``on_failure``, the value NI documents for an output (by
    terminal index) when the operation fails; ``source``, the node's name;
    ``kept``, the outputs (by terminal index) that keep what the operation had
    assigned when it failed."""

    failures: tuple[NativeError, ...]
    on_failure: dict[int, ScalarValue]
    source: str
    kept: frozenset[int] = frozenset()

    @classmethod
    def of_primitive(cls, entry: Mapping[str, Any]) -> CatalogFailures:
        """The failures of the primitive catalog entry ``entry``."""
        if "errors" not in entry:
            return cls((), {}, "")
        parsed = PrimitiveEntry.model_validate(entry)
        return cls(
            tuple(parsed.errors),
            {
                t.index: t.on_failure
                for t in parsed.terminals
                if t.on_failure is not None
            },
            parsed.name,
            frozenset(t.index for t in parsed.terminals if t.keeps_on_failure),
        )

    @classmethod
    def of_vilib(cls, entry: VIEntry) -> CatalogFailures:
        """The failures of the vi.lib catalog entry ``entry``."""
        return cls(
            tuple(entry.errors),
            {
                t.index: t.on_failure
                for t in entry.terminals
                if t.index is not None and t.on_failure is not None
            },
            entry.name,
        )


def own_exception_class(failure: NativeError) -> ast.ClassDef:
    """The class definition of the exception ``failure`` makes of the operation's
    own (``failure.base`` is its built-in base)."""
    [definition] = ast.parse(
        f"class {failure.exception}({failure.base}):\n"
        f'    """LabVIEW error {failure.code}."""'
    ).body
    assert isinstance(definition, ast.ClassDef)
    return definition


def invalid_reference_guards(
    node: AnyGraphNode, ctx: CodeGenContext, entry: VIEntry
) -> list[ast.stmt]:
    """For each failure of ``entry`` that names a reference input
    (``NativeError.invalid_input``), ``if isinstance(<input>, int): raise <it>``:
    a reference is an object, and a number there refers to nothing. The message
    is built when the code runs and names the value. An unwired input has none."""
    statements: list[ast.stmt] = []
    for failure in entry.errors:
        if failure.invalid_input is None:
            continue
        catalog_term = next(
            t
            for t in entry.terminals
            if t.python_param == failure.invalid_input
            and t.direction == TerminalDirection.INPUT
        )
        call_term = next(
            (
                t
                for t in getattr(node, "terminals", ())
                if t.index == catalog_term.index
                and t.direction == TerminalDirection.INPUT
            ),
            None,
        )
        value = ctx.wired_input(call_term.id, node) if call_term else None
        if value is None or value == "None":
            continue
        text = f"{entry.name}: the input {catalog_term.name!r} is not a reference: "
        [guard] = ast.parse(
            f"if isinstance({value}, int):\n"
            f"    raise {failure.exception}({text!r} + repr({value}))"
        ).body
        statements.append(guard)
    return statements


def catch_failures(
    node: AnyGraphNode,
    fragment: CodeFragment,
    catalog: CatalogFailures,
    ctx: CodeGenContext,
) -> CodeFragment:
    """``fragment``, the code of the catalog node ``node``, with each failure
    ``catalog`` lists caught and made its error out."""
    failures = tuple(
        f
        for f in catalog.failures
        if not f.only_with_class
        or any(
            contains_class_instance(t.lv_type)
            for t in getattr(node, "terminals", None) or ()
        )
    )
    _, outputs = own_error_terminals(node, ctx)
    # A class of the operation's own is raised whether or not it is caught; it
    # is registered with the module's other helpers, one definition per name,
    # and written under the name the module gives it (``add_module_function``).
    # Several nodes share a class (Delete, Create Folder and Write to Text File
    # all raise EmptyOrRelativePathError), so its definition names the error, not
    # the node.
    written: dict[str, str] = {}
    for failure in failures:
        if failure.base is not None:
            written[failure.exception] = ctx.add_module_function(
                own_exception_class(failure)
            )
    renamed = {old: new for old, new in written.items() if old != new}
    if renamed:
        fragment.statements = [_rename(s, renamed) for s in fragment.statements]
    if not failures or not fragment.statements:
        return fragment
    # With no error out wired the failure has nowhere to go: the VI's automatic
    # error handling decides. On, it stays a Python exception; off, it is
    # dropped and the run continues.
    error: str | None = None
    succeeded = "None"
    if any(ctx.is_wired(term.id) for term in outputs):
        if fragment.runs_on_error:
            error = ctx.make_output_var("own_error", node.id)
            fragment.own_error = error
        else:
            error = ctx.make_output_var("error_out", node.id, outputs[0].id)
            succeeded = _incoming_error(node, ctx)
            for term in outputs:
                fragment.bindings[term.id] = error
    elif automatic_error_handling(ctx):
        return fragment
    defaults = output_defaults(
        node,
        ctx,
        fragment.bindings,
        assigned_names(fragment.statements),
        None,
        {index: repr(value) for index, value in catalog.on_failure.items()},
        catalog.kept,
    )
    exception = ctx.make_output_var("exception", node.id)
    handlers = []
    for failure in failures:
        if failure.module is not None:
            fragment.imports.add(exception_import(failure, ctx))
        body = list(defaults)
        if error is not None:
            error_value = _labview_error(failure, catalog.source, exception)
            body.append(build_assign(error, error_value))
        handlers.append(
            ast.ExceptHandler(
                type=ast.Name(
                    id=written.get(failure.exception, exception_name(failure)),
                    ctx=ast.Load(),
                ),
                name=None if error is None else exception,
                body=body or [ast.Pass()],
            )
        )
    orelse: list[ast.stmt] = []
    if error is not None:
        ctx.import_labview_error()
        orelse = [build_assign(error, parse_expr(succeeded))]
    fragment.statements = [
        ast.Try(
            body=fragment.statements,
            handlers=handlers,
            orelse=orelse,
            finalbody=[],
        )
    ]
    return fragment


def automatic_error_handling(ctx: CodeGenContext) -> bool:
    """Whether the VI being generated has automatic error handling on (VI
    Properties > Execution). A VI whose setting is unknown (its save record is
    unparsed) is on, LabVIEW's default."""
    if ctx.graph is None or not ctx.vi_name:
        return True
    setting = ctx.graph.get_vi_properties(ctx.vi_name).execution.auto_error_handling
    return setting is not False


def raise_unwired_error(
    node: AnyGraphNode, bindings: dict[str, str], ctx: CodeGenContext
) -> list[ast.stmt]:
    """The statements after a call of a generated VI whose error out is not
    wired, in a VI with automatic error handling on: an error the call
    produced is raised, as LabVIEW stops at the node and shows the error. An
    error that only came in on the node's error in and passed through is not
    the node's own. With the setting off, nothing."""
    if not automatic_error_handling(ctx):
        return []
    error_in, outputs = own_error_terminals(node, ctx)
    incoming = ctx.wired_input(error_in.id, node) if error_in is not None else None
    statements: list[ast.stmt] = []
    for term in outputs:
        value = bindings.get(term.id)
        # A call's own terminal has an edge from the callee's pane even when
        # nothing is wired to it, so wired means it feeds something.
        if value is None or ctx.graph is None or ctx.graph.outgoing_edges(term.id):
            continue
        test = error_occurred(value)
        if incoming not in (None, "None"):
            test += f" and {value} is not {incoming}"
        [statement] = ast.parse(f"if {test}:\n    raise {value}").body
        statements.append(statement)
    if statements:
        ctx.import_labview_error()
    return statements


class _Rename(ast.NodeTransformer):
    """Every ``Name`` that is a key of ``names``, as its value."""

    def __init__(self, names: dict[str, str]) -> None:
        self.names = names

    def visit_Name(self, node: ast.Name) -> ast.Name:
        return ast.Name(id=self.names.get(node.id, node.id), ctx=node.ctx)


def _rename(statement: ast.stmt, names: dict[str, str]) -> ast.stmt:
    """``statement`` with the class names ``names`` maps written as their new
    names (a class of the operation's own the module writes under another)."""
    result = _Rename(names).visit(statement)
    assert isinstance(result, ast.stmt)
    return result


def exception_name(failure: NativeError) -> str:
    """The name generated code gives the exception class of ``failure``: a
    built-in by its own name; one from a module as ``<module>_<name>``, so it
    is never a generated variable's name (``struct.error`` is ``struct_error``,
    not ``error``)."""
    if failure.module is None:
        return failure.exception
    return f"{failure.module.replace('.', '_')}_{failure.exception}"


def exception_import(failure: NativeError, ctx: CodeGenContext) -> str:
    """The import of the exception class of ``failure``, as ``exception_name``.
    A class the package defines (``package_module``) is imported from that module
    where the package has it. Otherwise it comes from its Python module: an
    exception Python has only from version ``since_python`` on is imported where
    it exists; before it nothing can raise it, so the module defines a class of
    that name that is never raised."""
    assert failure.module is not None
    name = exception_name(failure)
    if failure.package_module is not None and isinstance(
        ctx.import_resolver, ImportResolver
    ):
        shared = ctx.import_resolver.shared_names_import(
            failure.package_module, f"{failure.exception} as {name}"
        )
        if shared is not None:
            return shared
    statement = f"from {failure.module} import {failure.exception} as {name}"
    if failure.since_python is None:
        return statement
    return (
        f"try:\n    {statement}\n"
        f"except ImportError:\n"
        f"    class {name}(Exception):\n"
        f'        """{failure.module}.{failure.exception}, which Python has from '
        f'{failure.since_python} on; before it nothing raises it."""\n'
    )


def _labview_error(failure: NativeError, source: str, exception: str) -> ast.expr:
    """``LabVIEWError(code=…, source=…, message=…)`` for ``failure``, caught as
    the variable ``exception``."""
    if failure.code_expression is not None:
        message = (failure.message_expression or "str({exception})").format(
            exception=exception
        )
        return parse_expr(
            f"{LABVIEW_ERROR_NAME}(code="
            f"{failure.code_expression.format(exception=exception)}, "
            f"source={source!r}, message={message})"
        )
    if failure.code_attribute is not None:
        return parse_expr(
            f"{LABVIEW_ERROR_NAME}(code={exception}.{failure.code_attribute}, "
            f"source={source!r}, message=str({exception}))"
        )
    code = GENERIC_ERROR_CODE if failure.code is None else failure.code
    return labview_error(code, source, f"str({exception})")


def labview_error(code: int, source: str, message: str | None = None) -> ast.expr:
    """``LabVIEWError(code=…, source=…, message=…)``: an error of ``code`` the
    node named ``source`` reports. ``message`` is the expression of its text,
    with NI's description of the code standing in when it is empty; without
    ``message`` the text is that description. The module imports
    ``LabVIEWError`` with ``CodeGenContext.import_labview_error``."""
    description = describe_error_code(code)
    if message is None:
        text = repr(description or "")
    elif description is not None:
        text = f"{message} or {description!r}"
    else:
        text = message
    return parse_expr(
        f"{LABVIEW_ERROR_NAME}(code={code}, source={source!r}, message={text})"
    )


def _incoming_error(node: AnyGraphNode, ctx: CodeGenContext) -> str:
    """The error that comes in on the node's error in; ``None`` when there is
    none or it is unwired."""
    error_in, _ = own_error_terminals(node, ctx)
    if error_in is None:
        return "None"
    return ctx.wired_input(error_in.id, node) or "None"
