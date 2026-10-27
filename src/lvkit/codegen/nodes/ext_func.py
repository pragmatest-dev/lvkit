"""Code generator for the Call Library Function Node (heap class ``extFunc``).

The node lowers to ``ctypes`` code written out in the module (see
``lvkit.parser.extfunc_prototype`` for how the node's prototype is read). The
library named in the node's setup is loaded once at module level and the
function's ``argtypes`` and ``restype`` are set there; a library named by a wired
``path in`` is loaded at the call instead (see ``ext_func_library`` for the
loader, wildcards and library files). Each
parameter is packed into the C type the prototype names right before the call (a
LabVIEW string or array handle and an Adapt to Type cluster are ``ctypes``
structures defined in the module, see ``ext_func_layout``) and read back right
after it; each output terminal binds to its row's value. An unwired input is the
parameter type's default.

A node that names the library ``LabVIEW`` calls one of LabVIEW's own functions,
which no library on disk provides: it is written out as the Python that
``data/labview_manager.json`` lists for that function (``ext_func_manager``).

The node's error terminals are not bound, like every other node here: a library
that cannot be loaded or called raises the exception ``ctypes`` raises.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import PureWindowsPath

from lvkit.graph.models import PrimitiveNode
from lvkit.manager_functions import LABVIEW_LIBRARY
from lvkit.models import LVType, Terminal
from lvkit.parser.extfunc_prototype import (
    UNPASSABLE_KINDS,
    ExtFuncCall,
    ExtFuncParam,
    ParamKind,
)

from ..ast_utils import to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .ext_func_layout import Layout, c_type, parameter_layout
from .ext_func_library import loader, static_name, wired_name
from .ext_func_manager import generate_manager_call
from .ext_func_marshal import Marshalled, default_value, marshal
from .primitive import _emit_unknown

_FLOAT_TYPES = ("float32", "float64")
_FLOAT_WIRES = ("NumFloat32", "NumFloat64")


@dataclass(frozen=True)
class _Row:
    """One prototype row with the layout its wire type gives it."""

    param: ExtFuncParam
    layout: Layout | None = None


def _terminal(node: PrimitiveNode, terminal_id: str | None) -> Terminal | None:
    return next((t for t in node.terminals if t.id == terminal_id), None)


def _wire_type(node: PrimitiveNode, terminal_id: str | None) -> LVType | None:
    terminal = _terminal(node, terminal_id)
    return terminal.lv_type if terminal else None


def _rows(node: PrimitiveNode, call: ExtFuncCall) -> tuple[list[_Row], str | None]:
    """The call's rows, and why the node cannot be generated (None when it can)."""
    if call.calling_convention not in ("stdcall", "c"):
        return [], f"its calling convention is {call.calling_convention}"
    if not call.params or len(call.params) != len(call.row_terminal_ids):
        return [], "its prototype could not be read"
    rows: list[_Row] = []
    for index, param in enumerate(call.params):
        where = "the return value is" if index == 0 else f"parameter {index} is"
        if param.kind in UNPASSABLE_KINDS:
            return rows, f"{where} {param.reason}"
        if index == 0 and param.kind not in (ParamKind.VALUE, ParamKind.NO_VALUE):
            return rows, f"{where} a {param.kind.value} return value"
        layout = None
        if param.kind == ParamKind.ADAPT_TO_TYPE:
            output_id = call.row_terminal_ids[index][1]
            terminal = _terminal(node, output_id)
            layout, why = parameter_layout(
                terminal.lv_type if terminal else None,
                (terminal.name if terminal else "") or "cluster",
            )
            if layout is None:
                return rows, f"{where} an Adapt to Type parameter {why}"
        rows.append(_Row(param, layout))
    return rows, None


def _is(lv_type: LVType | None, *names: str) -> bool:
    return lv_type is not None and lv_type.underlying_type in names


def _library_name(library: str) -> str:
    """The module-level name of a library: its file name as an identifier."""
    return to_var_name(PureWindowsPath(library).name.replace(".", "_"))


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Lower a Call Library Function Node to ``ctypes`` code."""
    call = node.ext_func
    if call is None:
        return _emit_unknown(node, None, ctx)
    if call.library == LABVIEW_LIBRARY:
        return generate_manager_call(node, call, ctx)
    rows, reason = _rows(node, call)
    if reason is not None:
        return _emit_unknown(node, None, ctx, reason=reason)

    path_id = call.path_in_terminal_id
    path_var = ctx.resolve(path_id) if path_id else None
    if not path_var and not call.library:
        return _emit_unknown(
            node, None, ctx, reason="it records no library and its path in is unwired"
        )

    module = ctx.ctypes_module
    function = (
        call.function if call.function.isidentifier() else to_var_name(call.function)
    )
    marshalled: list[Marshalled] = []
    for index, row in enumerate(rows[1:], start=1):
        input_id, output_id = call.row_terminal_ids[index]
        var = ctx.resolve(input_id) if input_id else None
        wire_in = _wire_type(node, input_id)
        marshalled.append(
            marshal(
                row.param,
                row.layout,
                var or default_value(row.param, row.layout),
                f"_{function}_{index}",
                module,
                text_in=_is(wire_in, "String"),
                text_out=_is(_wire_type(node, output_id), "String"),
                round_to_int=(
                    _is(wire_in, *_FLOAT_WIRES)
                    and (row.param.c_type or "") not in _FLOAT_TYPES
                ),
            )
        )

    argtypes = ", ".join(m.argtype for m in marshalled)
    return_param = rows[0].param
    restype = (
        "None"
        if return_param.kind == ParamKind.NO_VALUE
        else c_type(return_param.c_type or "")
    )
    load = loader(call)
    lines: list[str] = []
    imports = {"import ctypes"}
    if path_var:
        callee = f"_{function}_call"
        name, needed = wired_name(path_var, module)
        imports |= needed
        lines += [
            f"_{function}_library = {load}({name})",
            f"{callee} = _{function}_library[{call.function!r}]",
            f"{callee}.argtypes = [{argtypes}]",
            f"{callee}.restype = {restype}",
        ]
    else:
        library = call.library or ""
        name, needed = static_name(ctx, node, call, module)
        imports |= needed
        library_name = module.declare(
            ("library", load, name),
            f"_{_library_name(library)}",
            lambda n: f"{n} = {load}({name})",
        )
        callee = module.declare(
            ("function", library_name, call.function, argtypes, restype),
            f"{library_name}_{function}",
            lambda n: (
                f"{n} = {library_name}[{call.function!r}]\n"
                f"{n}.argtypes = [{argtypes}]\n"
                f"{n}.restype = {restype}\n"
            ),
        )
    for m in marshalled:
        lines += m.setup
    arguments = ", ".join(m.passed for m in marshalled)
    return _bind(node, call, lines, f"{callee}({arguments})", marshalled, ctx, imports)


def _bind(
    node: PrimitiveNode,
    call: ExtFuncCall,
    lines: list[str],
    invocation: str,
    marshalled: list[Marshalled],
    ctx: CodeGenContext,
    imports: set[str],
) -> CodeFragment:
    """``return = call(...)``, then each row's output terminal bound to its value
    after the call."""
    bindings: dict[str, str] = {}
    names: list[str | None] = []
    for row, (_, output_id) in enumerate(call.row_terminal_ids):
        terminal = _terminal(node, output_id)
        name = (
            ctx.make_output_var(
                terminal.name or f"param_{row}", node.id, terminal_id=terminal.id
            )
            if terminal
            else None
        )
        names.append(name)
        if name is not None:
            bindings[output_id] = name
    lines.append(f"{names[0] or '_'} = {invocation}")
    for m in marshalled:
        lines += m.after
    for name, m in zip(names[1:], marshalled):
        if name is not None:
            lines.append(f"{name} = {m.result}")
    if any("SimpleNamespace" in line for line in lines):
        imports.add("from types import SimpleNamespace")
    return CodeFragment(
        statements=ast.parse("\n".join(lines)).body,
        bindings=bindings,
        imports=imports,
    )
