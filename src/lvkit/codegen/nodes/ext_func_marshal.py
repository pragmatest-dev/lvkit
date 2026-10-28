"""How one parameter of a Call Library Function Node reaches the C function: the
statements that pack its input into a C value, the ``ctypes`` argument type and
the argument, and the expression that reads the parameter's value after the call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lvkit.parser.extfunc_prototype import ExtFuncParam, ParamKind

from ..ctypes_module import CtypesModule
from .ext_func_layout import (
    ArrayHandle,
    Cluster,
    Layout,
    Scalar,
    StringHandle,
    c_type,
    ctype_of,
    fill,
    fill_handle,
    read,
    read_handle,
)

_FLOAT_ELEMENTS = ("float32", "float64")


@dataclass
class Marshalled:
    """One parameter: ``setup`` runs before the call, ``passed`` is the argument
    of ``argtype``, ``after`` runs after it and ``result`` is the parameter's value
    then."""

    setup: list[str]
    argtype: str
    passed: str
    result: str
    after: list[str] = field(default_factory=list)


def default_value(param: ExtFuncParam, layout: Layout | None) -> str | None:
    """The value an unwired input has: its type's default (None for a cluster,
    whose structure is left zeroed)."""
    kind = param.kind
    if kind in (ParamKind.C_STRING, ParamKind.STRING_HANDLE):
        return "''"
    if kind in (ParamKind.ARRAY_DATA, ParamKind.ARRAY_HANDLE):
        return "[]"
    if kind == ParamKind.STRING_HANDLE_POINTER:
        return "''"
    if kind == ParamKind.ADAPT_TO_TYPE:
        if isinstance(layout, Scalar):
            return "False" if layout.element == "bool" else _zero(layout.element)
        return None
    return _zero(param.c_type or "")


def _zero(element: str) -> str:
    return "0.0" if element in _FLOAT_ELEMENTS else "0"


def marshal(
    param: ExtFuncParam,
    layout: Layout | None,
    value: str | None,
    prefix: str,
    module: CtypesModule,
    *,
    text_in: bool,
    text_out: bool,
    round_to_int: bool,
) -> Marshalled:
    """Parameter ``param`` whose input is the Python expression ``value`` (None
    for a cluster left unwired); ``prefix`` starts the name of every local."""
    kind = param.kind
    element = param.c_type or ""
    number = c_type(element)
    start = f"int(round({value}))" if round_to_int and value else value
    if kind == ParamKind.VALUE:
        return Marshalled([], number, start or "0", value or "0")
    if kind == ParamKind.POINTER:
        return Marshalled(
            [f"{prefix} = {number}({start})"],
            f"ctypes.POINTER({number})",
            f"ctypes.byref({prefix})",
            f"{prefix}.value",
        )
    if kind == ParamKind.C_STRING:
        return Marshalled(
            [f"{prefix} = ctypes.create_string_buffer({value}.encode('latin-1'))"],
            "ctypes.c_char_p",
            prefix,
            f"{prefix}.value.decode('latin-1')",
        )
    if kind == ParamKind.ARRAY_DATA:
        setup: list[str] = []
        items = value
        if text_in:
            items = f"{prefix}_items"
            setup.append(f"{items} = list({value}.encode('latin-1'))")
        setup.append(f"{prefix} = ({number} * len({items}))(*{items})")
        result = f"bytes({prefix}).decode('latin-1')" if text_out else f"list({prefix})"
        return Marshalled(setup, f"ctypes.POINTER({number})", prefix, result)
    if kind in (
        ParamKind.STRING_HANDLE,
        ParamKind.ARRAY_HANDLE,
        ParamKind.STRING_HANDLE_POINTER,
    ):
        handle_layout: StringHandle | ArrayHandle = (
            ArrayHandle(element, param.dimensions)
            if kind == ParamKind.ARRAY_HANDLE
            else StringHandle()
        )
        handle = f"{prefix}_handle"
        setup = fill_handle(handle_layout, value or "''", handle, prefix, module)
        after, result = read_handle(handle_layout, handle, prefix, module)
        handle_type = ctype_of(handle_layout, module)
        if kind == ParamKind.STRING_HANDLE_POINTER:
            return Marshalled(
                setup,
                f"ctypes.POINTER({handle_type})",
                f"ctypes.byref({handle})",
                result,
                after,
            )
        return Marshalled(setup, handle_type, handle, result, after)
    return _adapt(layout, value, prefix, module)


def _adapt(
    layout: Layout | None, value: str | None, prefix: str, module: CtypesModule
) -> Marshalled:
    """An Adapt to Type parameter: a pointer to the wire value's C layout."""
    if isinstance(layout, Scalar):
        number = c_type(layout.element)
        after, result = read(layout, f"{prefix}.value", prefix, module)
        return Marshalled(
            [f"{prefix} = {number}({value})"],
            f"ctypes.POINTER({number})",
            f"ctypes.byref({prefix})",
            result,
            after,
        )
    assert isinstance(layout, Cluster)
    structure = ctype_of(layout, module)
    setup = [f"{prefix} = {structure}()"]
    if value is not None:
        setup += fill(layout, value, prefix, prefix, module)
    after, result = read(layout, prefix, prefix, module)
    return Marshalled(
        setup, f"ctypes.POINTER({structure})", f"ctypes.byref({prefix})", result, after
    )
