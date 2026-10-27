"""The C prototype a Call Library Function Node's hover panel shows.

NI documents only that its dialog "Displays the C prototype for the function";
this text format is ours, built from the node's own call (see
``lvkit.parser.extfunc_prototype``): ``<return> [__stdcall ]<function>(<type>
<name>, ...);``.
"""

from __future__ import annotations

from ..graph.models import PrimitiveNode
from ..parser.extfunc_prototype import ExtFuncParam, ParamKind

_C_NAMES = {
    "int8": "int8_t",
    "int16": "int16_t",
    "int32": "int32_t",
    "int64": "int64_t",
    "uint8": "uint8_t",
    "uint16": "uint16_t",
    "uint32": "uint32_t",
    "uint64": "uint64_t",
    "float32": "float",
    "float64": "double",
    "intptr": "intptr_t",
    "uintptr": "uintptr_t",
}


def _c_type(param: ExtFuncParam) -> str:
    element = _C_NAMES.get(param.c_type or "", "void")
    if param.kind == ParamKind.VALUE:
        return element
    if param.kind in (ParamKind.POINTER, ParamKind.ARRAY_DATA):
        return f"{element} *"
    if param.kind == ParamKind.C_STRING:
        return "char *"
    if param.kind == ParamKind.STRING_HANDLE:
        return "LStrHandle"
    if param.kind == ParamKind.STRING_HANDLE_POINTER:
        return "LStrHandle *"
    if param.kind == ParamKind.NO_VALUE:
        return "void"
    # An array handle, an Adapt to Type parameter, or a type this reader does not
    # know: a bare pointer. (The name LabVIEW's own headers give an array handle
    # is unverified, so it is not guessed.)
    return "void *"


def ext_func_prototype(node: PrimitiveNode) -> str | None:
    """The node's prototype, or None for a node without a call or prototype."""
    call = node.ext_func
    if (
        call is None
        or not call.params
        or len(call.params) != len(call.row_terminal_ids)
    ):
        return None
    names = {t.id: t.name for t in node.terminals}
    arguments = []
    for row, (param, (input_id, _)) in enumerate(
        zip(call.params[1:], call.row_terminal_ids[1:]), start=1
    ):
        name = (names.get(input_id) if input_id else None) or f"arg{row}"
        arguments.append(
            f"{_c_type(param)}{'' if _c_type(param).endswith('*') else ' '}{name}"
        )
    convention = "__stdcall " if call.calling_convention == "stdcall" else ""
    return (
        f"{_c_type(call.params[0])} {convention}{call.function}"
        f"({', '.join(arguments)});"
    )
