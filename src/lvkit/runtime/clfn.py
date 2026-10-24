"""Call Library Function Node: calling a function in a shared library.

``call_library_function`` loads the library, converts each argument the way the
node's parameter says (see ``lvkit.parser.extfunc_prototype.ParamKind``), calls
the function, and returns the return value followed by each parameter's value
after the call. A parameter passed by value comes back as it went in; a pointer,
string or array parameter comes back as the function left it.

Strings are bytes in LabVIEW and ``latin-1`` text here, like the rest of the
runtime.
"""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Sequence
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
}

# One parameter: (kind, C element type). Kinds are the ``ParamKind`` values.
Signature = Sequence[tuple[str, str | None]]


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


def _scalar(c_type: str | None) -> Any:
    if c_type is None or c_type not in _C_TYPES:
        raise LabVIEWError(
            code=ERROR_CALL_FAILED, source="Call Library Function", message=str(c_type)
        )
    return _C_TYPES[c_type]


def _to_bytes(value: Any) -> bytes:
    return value.encode("latin-1") if isinstance(value, str) else bytes(value)


class _Argument:
    """One parameter: the object handed to the function and how to read the
    parameter back after the call."""

    def __init__(self, kind: str, c_type: str | None, value: Any) -> None:
        self.kind = kind
        self.value = value
        self.was_text = isinstance(value, str)
        if kind == "value":
            self.cell = _scalar(c_type)(value)
            self.passed: Any = self.cell
        elif kind == "pointer":
            self.cell = _scalar(c_type)(value)
            self.passed = ctypes.byref(self.cell)
        elif kind == "c_string":
            data = _to_bytes(value)
            self.cell = ctypes.create_string_buffer(data, len(data) + 1)
            self.passed = self.cell
        elif kind == "array_data":
            elements = list(_to_bytes(value)) if self.was_text else list(value)
            self.cell = (_scalar(c_type) * len(elements))(*elements)
            self.passed = self.cell
        else:
            raise LabVIEWError(
                code=ERROR_CALL_FAILED,
                source="Call Library Function",
                message=f"unsupported parameter kind {kind!r}",
            )

    @property
    def argtype(self) -> Any:
        if self.kind == "value":
            return type(self.cell)
        if self.kind == "pointer":
            return ctypes.POINTER(type(self.cell))
        return ctypes.c_void_p

    def result(self) -> Any:
        if self.kind in ("value", "pointer"):
            return self.cell.value
        if self.kind == "c_string":
            return self.cell.value.decode("latin-1")
        elements = list(self.cell)
        return bytes(elements).decode("latin-1") if self.was_text else elements


def call_library_function(
    library: str,
    function: str,
    calling_convention: str,
    signature: Signature,
    args: Sequence[Any],
) -> tuple[Any, ...]:
    """Call ``function`` in ``library``.

    ``signature`` has one ``(kind, C type)`` per row, the return value first
    (kind ``no_value`` for a function with none); ``args`` has one value per
    parameter row after the return value. Returns the return value followed by
    each parameter's value after the call."""
    return_kind, return_type = signature[0]
    arguments = [
        _Argument(kind, c_type, value)
        for (kind, c_type), value in zip(signature[1:], args)
    ]
    lib = _load(library, calling_convention)
    try:
        func = getattr(lib, function)
    except AttributeError as exc:
        raise LabVIEWError(
            code=ERROR_LIBRARY_NOT_FOUND,
            source=f"{library}:{function}",
            message=str(exc),
        ) from exc
    func.argtypes = [a.argtype for a in arguments]
    func.restype = None if return_kind == "no_value" else _scalar(return_type)
    returned = func(*[a.passed for a in arguments])
    return (returned, *(a.result() for a in arguments))
