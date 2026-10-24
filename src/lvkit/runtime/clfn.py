"""Call Library Function Node: calling a function in a shared library.

``call_library_function`` loads the library, converts each argument the way the
node's parameter says (see ``lvkit.parser.extfunc_prototype.ParamKind``), calls
the function, and returns the return value followed by each parameter's value
after the call. A parameter passed by value comes back as it went in; a pointer,
string, array or handle parameter comes back as the function left it.

Strings are bytes in LabVIEW and ``latin-1`` text here, like the rest of the
runtime.

Each parameter is ``(kind, spec)``. A ``spec`` names a type:

- a number: ``int8`` .. ``uint64``, ``float32``, ``float64``, ``intptr``,
  ``uintptr``, or ``bool`` (LabVIEW stores a Boolean as 8 bits);
- ``("string",)``: a LabVIEW string, inside a cluster or behind a handle;
- ``("array", element, dimensions)``: a LabVIEW array of numbers;
- ``("cluster", named, ((attribute, spec), ...))``: a cluster. A named cluster is
  a ``SimpleNamespace`` and an anonymous one a tuple, as everywhere else.

Memory layouts, from NI's "How LabVIEW Stores Data in Memory" and "LabVIEW
Manager Data Types":

- A string handle points to a pointer to ``{int32 count; bytes}``; a NULL handle
  or pointer is an empty string.
- An array handle points to a pointer to the dimension sizes, each a 32-bit
  integer, followed by the elements; a NULL handle is an empty array. Elements
  up to 4 bytes wide follow the dimension sizes with no padding.
- A cluster is a C structure with natural alignment. Scalars sit in line,
  nested clusters in line, and strings and arrays are handles.
"""

from __future__ import annotations

import ctypes
import functools
import math
import struct
import sys
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from lvkit.labview_error import LabVIEWError

# LabVIEW error codes for a library that cannot be loaded and for a call that
# fails (NI: error 7 is a path that does not exist, error 1097 is an exception
# in the called code).
ERROR_LIBRARY_NOT_FOUND = 7
ERROR_CALL_FAILED = 1097

_C_TYPES: dict[str, Any] = {
    "int8": ctypes.c_int8,
    "int16": ctypes.c_int16,
    "int32": ctypes.c_int32,
    "int64": ctypes.c_int64,
    "uint8": ctypes.c_uint8,
    "uint16": ctypes.c_uint16,
    "uint32": ctypes.c_uint32,
    "uint64": ctypes.c_uint64,
    "float32": ctypes.c_float,
    "float64": ctypes.c_double,
    "intptr": ctypes.c_ssize_t,
    "uintptr": ctypes.c_size_t,
    "bool": ctypes.c_uint8,
}
_FLOATS = ("float32", "float64")

Spec = Any  # a type name or a nested tuple, see the module docstring
# One parameter: (kind, spec). Kinds are the ``ParamKind`` values.
Signature = Sequence[tuple[str, Spec]]


def _fail(message: str, source: str = "Call Library Function") -> LabVIEWError:
    return LabVIEWError(code=ERROR_CALL_FAILED, source=source, message=message)


def _load(library: str, calling_convention: str) -> Any:
    """The library, opened for ``calling_convention`` (``stdcall`` is a Windows
    convention; elsewhere every function is a plain C call)."""
    loader = (
        ctypes.WinDLL
        if calling_convention == "stdcall" and sys.platform == "win32"
        else ctypes.CDLL
    )
    try:
        return loader(library)
    except OSError as exc:
        raise LabVIEWError(
            code=ERROR_LIBRARY_NOT_FOUND, source=library, message=str(exc)
        ) from exc


def _to_bytes(value: Any) -> bytes:
    return value.encode("latin-1") if isinstance(value, str) else bytes(value)


@functools.cache
def _ctype(spec: Spec) -> Any:
    """The ctypes type of a value of ``spec`` as it sits in memory."""
    if isinstance(spec, str):
        if spec not in _C_TYPES:
            raise _fail(f"unknown type {spec!r}")
        return _C_TYPES[spec]
    if spec[0] in ("string", "array"):
        return ctypes.c_void_p  # a handle
    if spec[0] == "cluster":
        return type(
            "Cluster",
            (ctypes.Structure,),
            {
                "_fields_": [
                    (f"f{i}", _ctype(sub)) for i, (_, sub) in enumerate(spec[2])
                ]
            },
        )
    raise _fail(f"unknown type {spec!r}")


def _element_bytes(element: str) -> int:
    return ctypes.sizeof(_C_TYPES[element])


# ---------------------------------------------------------------- handles


def _new_string_handle(value: Any, keep: list[Any]) -> int:
    """A string handle holding ``value``: the address of a pointer to
    ``{int32 count; bytes}``."""
    data = _to_bytes(value)
    block = ctypes.create_string_buffer(4 + len(data))
    struct.pack_into("=i", block, 0, len(data))
    block[4 : 4 + len(data)] = data
    cell = ctypes.c_void_p(ctypes.addressof(block))
    keep += [block, cell]
    return ctypes.addressof(cell)


def _read_string(handle: int) -> str:
    block = ctypes.c_void_p.from_address(handle).value
    if not block:
        return ""
    count = ctypes.c_int32.from_address(block).value
    return ctypes.string_at(block + 4, max(count, 0)).decode("latin-1")


def _shape(value: Any, dimensions: int) -> tuple[list[int], list[Any]]:
    """The sizes of a nested list and its elements in row-major order."""
    if dimensions == 1:
        return [len(value)], list(value)
    rows = [_shape(row, dimensions - 1) for row in value]
    inner = rows[0][0] if rows else [0] * (dimensions - 1)
    if any(sizes != inner for sizes, _ in rows):
        raise _fail("an array parameter is not rectangular")
    return [len(rows), *inner], [item for _, flat in rows for item in flat]


def _nest(flat: list[Any], sizes: Sequence[int]) -> list[Any]:
    if len(sizes) == 1:
        return flat
    step = math.prod(sizes[1:])
    return [_nest(flat[i * step : (i + 1) * step], sizes[1:]) for i in range(sizes[0])]


def _new_array_handle(
    value: Any, element: str, dimensions: int, keep: list[Any]
) -> int:
    """An array handle holding ``value``: the address of a pointer to the
    dimension sizes (32-bit integers) followed by the elements."""
    sizes, flat = _shape(value, dimensions)
    header = 4 * dimensions
    kind = _C_TYPES[element]
    block = ctypes.create_string_buffer(header + len(flat) * _element_bytes(element))
    struct.pack_into(f"={dimensions}i", block, 0, *sizes)
    (kind * len(flat)).from_buffer(block, header)[:] = [
        float(x) if element in _FLOATS else int(x) for x in flat
    ]
    cell = ctypes.c_void_p(ctypes.addressof(block))
    keep += [block, cell]
    return ctypes.addressof(cell)


def _read_array(handle: int, element: str, dimensions: int) -> list[Any]:
    block = ctypes.c_void_p.from_address(handle).value
    if not block:
        return []
    sizes = struct.unpack(f"={dimensions}i", ctypes.string_at(block, 4 * dimensions))
    count = math.prod(sizes)
    kind = _C_TYPES[element]
    flat = list((kind * count).from_address(block + 4 * dimensions))
    return _nest(flat, sizes) if count else []


# ----------------------------------------------------------- spec <-> memory


def _member(value: Any, index: int, name: str) -> Any:
    """Member ``index`` of a cluster value: by position for a tuple or list, by
    the dictionary's ordered values, else by attribute ``name``."""
    if isinstance(value, (tuple, list)):
        return value[index]
    if isinstance(value, dict):
        return list(value.values())[index]
    return getattr(value, name)


def _to_c(spec: Spec, value: Any, keep: list[Any]) -> Any:
    """``value`` as ctypes takes it for a field or cell of ``spec``."""
    if isinstance(spec, str):
        if spec == "bool":
            return int(bool(value))
        return float(value) if spec in _FLOATS else int(value)
    if spec[0] == "string":
        return _new_string_handle(value, keep)
    if spec[0] == "array":
        return _new_array_handle(value, spec[1], spec[2], keep)
    members = _ctype(spec)()
    for i, (name, sub) in enumerate(spec[2]):
        member = _member(value, i, name)
        setattr(members, f"f{i}", _to_c(sub, member, keep))
    return members


def _from_c(spec: Spec, value: Any) -> Any:
    """The Python value of a field or cell of ``spec`` read from memory."""
    if isinstance(spec, str):
        return bool(value) if spec == "bool" else value
    if spec[0] == "string":
        return _read_string(value)
    if spec[0] == "array":
        return _read_array(value, spec[1], spec[2])
    values = [
        _from_c(sub, getattr(value, f"f{i}")) for i, (_, sub) in enumerate(spec[2])
    ]
    if spec[1]:
        return SimpleNamespace(**{name: v for (name, _), v in zip(spec[2], values)})
    return tuple(values)


class _Argument:
    """One parameter: the object handed to the function and how to read the
    parameter back after the call."""

    def __init__(self, kind: str, spec: Spec, value: Any, keep: list[Any]) -> None:
        self.kind = kind
        self.spec = spec
        self.was_text = isinstance(value, str)
        self.cell: Any
        if kind == "value":
            self.cell = _ctype(spec)(_to_c(spec, value, keep))
            self.passed: Any = self.cell
            self.argtype: Any = _ctype(spec)
        elif kind == "pointer":
            if isinstance(spec, str):
                self.cell = _ctype(spec)(_to_c(spec, value, keep))
            else:
                self.cell = _to_c(spec, value, keep)
            self.passed = ctypes.byref(self.cell)
            self.argtype = ctypes.POINTER(type(self.cell))
        elif kind == "c_string":
            data = _to_bytes(value)
            self.cell = ctypes.create_string_buffer(data, len(data) + 1)
            self.passed, self.argtype = self.cell, ctypes.c_void_p
        elif kind == "array_data":
            elements = list(_to_bytes(value)) if self.was_text else list(value)
            self.cell = (_ctype(spec) * len(elements))(*elements)
            self.passed, self.argtype = self.cell, ctypes.c_void_p
        elif kind == "string_handle":
            self.cell = _new_string_handle(value, keep)
            self.passed, self.argtype = ctypes.c_void_p(self.cell), ctypes.c_void_p
        elif kind == "array_handle":
            self.cell = _new_array_handle(value, spec[1], spec[2], keep)
            self.passed, self.argtype = ctypes.c_void_p(self.cell), ctypes.c_void_p
        else:
            raise _fail(f"unsupported parameter kind {kind!r}")

    def result(self) -> Any:
        if self.kind == "value":
            return _from_c(self.spec, self.cell.value)
        if self.kind == "pointer":
            if isinstance(self.spec, str):
                return _from_c(self.spec, self.cell.value)
            return _from_c(self.spec, self.cell)
        if self.kind == "c_string":
            return self.cell.value.decode("latin-1")
        if self.kind == "string_handle":
            return _read_string(self.cell)
        if self.kind == "array_handle":
            return _read_array(self.cell, self.spec[1], self.spec[2])
        elements = list(self.cell)
        return bytes(elements).decode("latin-1") if self.was_text else elements


def _call(
    func: Any, signature: Signature, args: Sequence[Any], *, set_prototype: bool
) -> tuple[Any, ...]:
    (return_kind, return_spec), parameters = signature[0], signature[1:]
    keep: list[Any] = []
    arguments = [
        _Argument(kind, spec, value, keep)
        for (kind, spec), value in zip(parameters, args)
    ]
    if set_prototype:
        func.argtypes = [a.argtype for a in arguments]
        func.restype = None if return_kind == "no_value" else _ctype(return_spec)
    returned = func(*[a.passed for a in arguments])
    return (returned, *(a.result() for a in arguments))


def call_library_function(
    library: str,
    function: str,
    calling_convention: str,
    signature: Signature,
    args: Sequence[Any],
) -> tuple[Any, ...]:
    """Call ``function`` in ``library``.

    ``signature`` has one ``(kind, spec)`` per row, the return value first (kind
    ``no_value`` for a function with none); ``args`` has one value per parameter
    row after the return value. Returns the return value followed by each
    parameter's value after the call."""
    lib = _load(library, calling_convention)
    try:
        func = getattr(lib, function)
    except AttributeError as exc:
        raise LabVIEWError(
            code=ERROR_LIBRARY_NOT_FOUND,
            source=f"{library}:{function}",
            message=str(exc),
        ) from exc
    return _call(func, signature, args, set_prototype=True)


def call_function_pointer(
    function: Any, signature: Signature, args: Sequence[Any]
) -> tuple[Any, ...]:
    """The same call as ``call_library_function``, on a function pointer that
    already has its prototype (a ``ctypes.CFUNCTYPE`` instance). Only the tests
    use it: it lets them call a C-callable function they define instead of a
    function in a library on disk."""
    return _call(function, signature, args, set_prototype=False)
