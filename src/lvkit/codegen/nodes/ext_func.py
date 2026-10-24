"""Code generator for the Call Library Function Node (heap class ``extFunc``).

The node lowers to one call into ``lvkit.runtime.clfn``, which loads the
library and marshals each parameter the way the node's prototype says (see
``lvkit.parser.extfunc_prototype``). The call returns the return value and
every parameter's value after the call; each output terminal binds to its
row's value. An unwired input is the parameter type's default. The library is
the wired ``path in`` when there is one, else the library the node records.

The node's error terminals are not bound, like every other node here: a library
that cannot be loaded or called raises ``LabVIEWError`` (NI error 7 / 1097), and
error wires are the exception model's job (``codegen/error_handler.py``).
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.parser.extfunc_prototype import (
    NUMERIC_C_TYPES,
    ExtFuncCall,
    ExtFuncParam,
    ParamKind,
)

from ..ast_utils import build_multi_assign, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .primitive import _emit_unknown

_CLFN_IMPORT = "from lvkit.runtime import clfn as _clfn"
_FLOAT_TYPES = ("float32", "float64")


def _default(param: ExtFuncParam) -> ast.expr:
    """The value an unwired input has."""
    if param.kind == ParamKind.C_STRING:
        return ast.Constant(value="")
    if param.kind == ParamKind.ARRAY_DATA:
        return ast.List(elts=[], ctx=ast.Load())
    return ast.Constant(value=0.0 if param.c_type in _FLOAT_TYPES else 0)


def _adapted(node: PrimitiveNode, row: int, call: ExtFuncCall) -> ExtFuncParam:
    """An Adapt to Type parameter as a pointer to its wire type, when that type
    is a number; otherwise an unsupported parameter."""
    output_id = call.row_terminal_ids[row][1]
    terminal = next((t for t in node.terminals if t.id == output_id), None)
    wire_type = (
        terminal.lv_type.underlying_type if terminal and terminal.lv_type else None
    )
    c_type = NUMERIC_C_TYPES.get(wire_type or "")
    if c_type is None:
        return ExtFuncParam(
            ParamKind.UNSUPPORTED,
            reason=f"an Adapt to Type parameter of type {wire_type or 'unknown'}",
        )
    return ExtFuncParam(ParamKind.POINTER, c_type)


def _parameters(
    node: PrimitiveNode, call: ExtFuncCall
) -> tuple[list[ExtFuncParam], str | None]:
    """The call's parameters with Adapt to Type settled, and why the node cannot
    be generated (None when it can)."""
    if call.calling_convention not in ("stdcall", "c"):
        return [], f"its calling convention is {call.calling_convention}"
    if not call.params or len(call.params) != len(call.row_terminal_ids):
        return [], "its prototype could not be read"
    params = [
        _adapted(node, row, call) if p.kind == ParamKind.ADAPT_TO_TYPE else p
        for row, p in enumerate(call.params)
    ]
    for index, param in enumerate(params):
        if param.kind == ParamKind.UNSUPPORTED:
            where = "the return value is" if index == 0 else f"parameter {index} is"
            return params, f"{where} {param.reason}"
    return params, None


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Lower a Call Library Function Node to a ``clfn.call_library_function``
    call."""
    call = node.ext_func
    if call is None:
        return _emit_unknown(node, None, ctx)
    params, reason = _parameters(node, call)
    if reason is not None:
        return _emit_unknown(node, None, ctx, reason=reason)

    path_var = (
        ctx.resolve(call.path_in_terminal_id) if call.path_in_terminal_id else None
    )
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

    args: list[ast.expr] = []
    for (input_id, _), param in zip(call.row_terminal_ids[1:], params[1:]):
        var = ctx.resolve(input_id) if input_id else None
        args.append(parse_expr(var) if var else _default(param))

    signature = ast.Tuple(
        elts=[
            ast.Tuple(
                elts=[ast.Constant(value=p.kind.value), ast.Constant(value=p.c_type)],
                ctx=ast.Load(),
            )
            for p in params
        ],
        ctx=ast.Load(),
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
            signature,
            ast.List(elts=args, ctx=ast.Load()),
        ],
        keywords=[],
    )

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
        imports={_CLFN_IMPORT},
    )
