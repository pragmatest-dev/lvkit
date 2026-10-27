"""Code generator for the Call Library Function Node (heap class ``extFunc``).

The node lowers to one call into ``lvkit.runtime.clfn``, which loads the
library and marshals each parameter the way the node's prototype says (see
``lvkit.parser.extfunc_prototype``). The call returns the return value and
every parameter's value after the call; each output terminal binds to its
row's value. An unwired input is the parameter type's default. The library is
the wired ``path in`` when there is one, else the library the node records.

A node that names the library ``LabVIEW`` calls one of LabVIEW's own functions,
which no library on disk provides: it lowers to the Python equivalent that
``data/labview_manager.json`` lists for that function, called with the same
arguments and returning the same values.

The node's error terminals are not bound, like every other node here: a library
that cannot be loaded or called raises ``LabVIEWError`` (NI error 7 / 1097), and
error wires are the exception model's job (``codegen/error_handler.py``).
"""

from __future__ import annotations

import ast
from typing import Any

from lvkit.graph.models import PrimitiveNode
from lvkit.manager_functions import LABVIEW_LIBRARY, manager_functions
from lvkit.parser.extfunc_prototype import (
    UNPASSABLE_KINDS,
    ExtFuncCall,
    ParamKind,
)

from ..ast_utils import build_multi_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .ext_func_adapt import adapt_spec
from .primitive import _emit_unknown

_CLFN_IMPORT = "from lvkit.runtime import clfn as _clfn"
_FLOAT_TYPES = ("float32", "float64")


def _default(kind: str, spec: Any) -> ast.expr:
    """The value an unwired input has: its type's default."""
    if kind == ParamKind.C_STRING.value or kind == ParamKind.STRING_HANDLE.value:
        return ast.Constant(value="")
    if kind in (ParamKind.ARRAY_DATA.value, ParamKind.ARRAY_HANDLE.value):
        return ast.List(elts=[], ctx=ast.Load())
    if kind == ParamKind.ADAPT_TO_TYPE.value:
        return ast.Constant(value=None)  # a wire type the function receives as is
    return _spec_default(spec)


def _spec_default(spec: Any) -> ast.expr:
    if spec == "bool":
        return ast.Constant(value=False)
    if isinstance(spec, str):
        return ast.Constant(value=0.0 if spec in _FLOAT_TYPES else 0)
    if spec[0] == "string":
        return ast.Constant(value="")
    if spec[0] == "array":
        return ast.List(elts=[], ctx=ast.Load())
    return ast.Tuple(elts=[_spec_default(sub) for _, sub in spec[2]], ctx=ast.Load())


def _entry(
    node: PrimitiveNode, row: int, call: ExtFuncCall
) -> tuple[tuple[str, Any], str]:
    """Row ``row``'s ``(kind, spec)`` as ``lvkit.runtime.clfn`` takes it, and why
    it has none ('' when it has)."""
    param = call.params[row]
    if param.kind == ParamKind.ADAPT_TO_TYPE:
        output_id = call.row_terminal_ids[row][1]
        terminal = next((t for t in node.terminals if t.id == output_id), None)
        spec, why = adapt_spec(terminal.lv_type if terminal else None)
        if spec is None:
            return ("unsupported", None), f"an Adapt to Type parameter {why}"
        return (ParamKind.POINTER.value, spec), ""
    if param.kind in UNPASSABLE_KINDS:
        return ("unsupported", None), param.reason
    if param.kind == ParamKind.ARRAY_HANDLE:
        return (param.kind.value, ("array", param.c_type, param.dimensions)), ""
    return (param.kind.value, param.c_type), ""


def _signature(
    node: PrimitiveNode, call: ExtFuncCall
) -> tuple[list[tuple[str, Any]], str | None]:
    """The call's ``(kind, spec)`` per row, and why the node cannot be generated
    (None when it can)."""
    if call.calling_convention not in ("stdcall", "c"):
        return [], f"its calling convention is {call.calling_convention}"
    if not call.params or len(call.params) != len(call.row_terminal_ids):
        return [], "its prototype could not be read"
    entries: list[tuple[str, Any]] = []
    for row in range(len(call.params)):
        entry, why = _entry(node, row, call)
        if why:
            where = "the return value is" if row == 0 else f"parameter {row} is"
            return entries, f"{where} {why}"
        entries.append(entry)
    return entries, None


def _constant(value: Any) -> ast.expr:
    """``value`` (strings, numbers, booleans, None, nested tuples) as an AST."""
    if isinstance(value, tuple):
        return ast.Tuple(elts=[_constant(v) for v in value], ctx=ast.Load())
    return ast.Constant(value=value)


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Lower a Call Library Function Node to a call into the runtime."""
    call = node.ext_func
    if call is None:
        return _emit_unknown(node, None, ctx)
    if call.library == LABVIEW_LIBRARY:
        return _generate_manager_call(node, call, ctx)
    entries, reason = _signature(node, call)
    if reason is not None:
        return _emit_unknown(node, None, ctx, reason=reason)

    path_id = call.path_in_terminal_id
    path_var = ctx.resolve(path_id) if path_id else None
    if path_var:
        library: ast.expr = ast.Call(
            func=ast.Name(id="str", ctx=ast.Load()),
            args=[parse_expr(path_var)],
            keywords=[],
        )
    elif call.library:
        library = ast.Constant(value=call.library)
    else:
        return _emit_unknown(
            node, None, ctx, reason="it records no library and its path in is unwired"
        )

    invocation = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id="_clfn", ctx=ast.Load()),
            attr="call_library_function",
            ctx=ast.Load(),
        ),
        args=[
            library,
            ast.Constant(value=call.function),
            ast.Constant(value=call.calling_convention),
            _constant(tuple(entries)),
            ast.List(elts=_arguments(call, entries, ctx), ctx=ast.Load()),
        ],
        keywords=[],
    )
    return _bind(node, call, invocation, {_CLFN_IMPORT}, ctx)


def _generate_manager_call(
    node: PrimitiveNode, call: ExtFuncCall, ctx: CodeGenContext
) -> CodeFragment:
    """One of LabVIEW's own functions, as the Python that
    ``data/labview_manager.json`` names for it."""
    function = manager_functions().get(call.function)
    if function is None:
        return _emit_unknown(
            node,
            None,
            ctx,
            reason=f"LabVIEW's function {call.function} has no Python equivalent",
        )
    if len(function.parameters) != len(call.row_terminal_ids) - 1:
        return _emit_unknown(
            node,
            None,
            ctx,
            reason=(
                f"it has {len(call.row_terminal_ids) - 1} parameters but NI's "
                f"{call.function} has {len(function.parameters)}"
            ),
        )
    entries = [(p.kind.value, p.c_type) for p in call.params]
    invocation = ast.Call(
        func=parse_expr(function.python),
        args=_arguments(call, entries, ctx),
        keywords=[],
    )
    return _bind(node, call, invocation, {function.import_statement}, ctx)


def _arguments(
    call: ExtFuncCall, entries: list[tuple[str, Any]], ctx: CodeGenContext
) -> list[ast.expr]:
    """The input value of every parameter row (the return value has none); an
    unwired input is its type's default."""
    arguments: list[ast.expr] = []
    for (input_id, _), (kind, spec) in zip(call.row_terminal_ids[1:], entries[1:]):
        var = ctx.resolve(input_id) if input_id else None
        arguments.append(parse_expr(var) if var else _default(kind, spec))
    return arguments


def _bind(
    node: PrimitiveNode,
    call: ExtFuncCall,
    invocation: ast.expr,
    imports: set[str],
    ctx: CodeGenContext,
) -> CodeFragment:
    """``return, out1, out2 = <invocation>``: every row's output terminal bound
    to its value."""
    by_id = {t.id: t for t in node.terminals}
    targets: list[str] = []
    bindings: dict[str, str] = {}
    for row, (_, output_id) in enumerate(call.row_terminal_ids):
        terminal = by_id.get(output_id)
        if terminal is None:
            targets.append("_")
            continue
        name = ctx.make_output_var(
            terminal.name or f"param_{row}", node.id, terminal_id=terminal.id
        )
        bindings[terminal.id] = name
        targets.append(name)
    return CodeFragment(
        statements=[build_multi_assign(targets, invocation)],
        bindings=bindings,
        imports=imports,
    )
