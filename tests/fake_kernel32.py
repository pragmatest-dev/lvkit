"""A stand-in for the few kernel32 functions OpenG's large-file VIs call, built
on ordinary file descriptors, so those VIs' generated Python can run end to end
on a machine without Windows.

It follows Microsoft's documentation of each function (learn.microsoft.com,
fileapi.h and handleapi.h):

- CreateFileA's creation dispositions CREATE_NEW 1, CREATE_ALWAYS 2,
  OPEN_EXISTING 3, OPEN_ALWAYS 4 and TRUNCATE_EXISTING 5, and its error codes
  ERROR_FILE_NOT_FOUND 2 and ERROR_FILE_EXISTS 80;
- SetFilePointer's FILE_BEGIN 0, FILE_CURRENT 1 and FILE_END 2: it returns the low
  DWORD of the new position and stores the high DWORD through its pointer;
- GetFileSize's low DWORD and ``lpFileSizeHigh``, and GetFileSizeEx's 64-bit
  size through ``lpFileSize``;
- SetEndOfFile, which sets the file size at the file pointer;
- ReadFile and WriteFile, which move the file pointer and report the byte count
  through ``lpNumberOfBytesRead`` / ``lpNumberOfBytesWritten``;
- CloseHandle and GetLastError.

It replaces ``clfn.call_library_function`` for calls to ``kernel32.dll`` only; a
call to any other library goes to the real one.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Callable, Sequence
from typing import Any

import pytest

from lvkit.runtime import clfn

GENERIC_WRITE = 0x40000000
CREATE_NEW, CREATE_ALWAYS, OPEN_EXISTING, OPEN_ALWAYS, TRUNCATE_EXISTING = 1, 2, 3, 4, 5
INVALID_HANDLE_VALUE = 0xFFFFFFFF
INVALID_SET_FILE_POINTER = 0xFFFFFFFF
INVALID_FILE_SIZE = 0xFFFFFFFF
ERROR_FILE_NOT_FOUND, ERROR_INVALID_HANDLE, ERROR_FILE_EXISTS = 2, 6, 80

_FORMATS = {
    "int8": "b",
    "uint8": "B",
    "int16": "h",
    "uint16": "H",
    "int32": "i",
    "uint32": "I",
    "int64": "q",
    "uint64": "Q",
    "float32": "f",
    "float64": "d",
}


class Kernel32:
    """The state of the stand-in: open files by handle and the last error."""

    def __init__(self) -> None:
        self.files: dict[int, int] = {}  # handle -> file descriptor
        self.last_error = 0
        self._next = 0x100

    def _handle(self, handle: int) -> int | None:
        if handle not in self.files:
            self.last_error = ERROR_INVALID_HANDLE
            return None
        return self.files[handle]

    def CreateFileA(
        self,
        name: str,
        access: int,
        share: int,
        security: int,
        disposition: int,
        flags: int,
        template: int,
    ) -> int:
        exists = os.path.exists(name)
        if disposition == CREATE_NEW and exists:
            self.last_error = ERROR_FILE_EXISTS
            return INVALID_HANDLE_VALUE
        if disposition in (OPEN_EXISTING, TRUNCATE_EXISTING) and not exists:
            self.last_error = ERROR_FILE_NOT_FOUND
            return INVALID_HANDLE_VALUE
        mode = os.O_RDWR | os.O_CREAT
        if disposition in (CREATE_ALWAYS, TRUNCATE_EXISTING):
            mode |= os.O_TRUNC
        descriptor = os.open(name, mode)
        self._next += 4
        self.files[self._next] = descriptor
        self.last_error = 0
        return self._next

    def ReadFile(
        self, handle: int, buffer: Sequence[int], count: int, element: str
    ) -> tuple[list[int], int] | None:
        descriptor = self._handle(handle)
        if descriptor is None:
            return None
        data = os.read(descriptor, count)
        code = _FORMATS[element]
        size = struct.calcsize(code)
        whole = len(data) // size
        values = list(struct.unpack(f"<{whole}{code}", data[: whole * size]))
        return values + list(buffer)[whole:], len(data)

    def WriteFile(
        self, handle: int, buffer: Sequence[int], count: int, element: str
    ) -> int | None:
        descriptor = self._handle(handle)
        if descriptor is None:
            return None
        code = _FORMATS[element]
        raw = struct.pack(f"<{len(buffer)}{code}", *buffer)[:count]
        return os.write(descriptor, raw)

    def SetFilePointer(
        self, handle: int, low: int, high: int | None, method: int
    ) -> tuple[int, int]:
        descriptor = self._handle(handle)
        if descriptor is None:
            return INVALID_SET_FILE_POINTER, 0
        distance = struct.unpack(
            "<q", struct.pack("<II", low & 0xFFFFFFFF, (high or 0) & 0xFFFFFFFF)
        )[0]
        position = os.lseek(
            descriptor, distance, (os.SEEK_SET, os.SEEK_CUR, os.SEEK_END)[method]
        )
        return position & 0xFFFFFFFF, position >> 32

    def GetFileSize(self, handle: int) -> tuple[int, int]:
        descriptor = self._handle(handle)
        if descriptor is None:
            return INVALID_FILE_SIZE, 0
        size = os.fstat(descriptor).st_size
        return size & 0xFFFFFFFF, size >> 32

    def SetEndOfFile(self, handle: int) -> int:
        descriptor = self._handle(handle)
        if descriptor is None:
            return 0
        os.ftruncate(descriptor, os.lseek(descriptor, 0, os.SEEK_CUR))
        return 1

    def GetFileSizeEx(self, handle: int) -> tuple[int, int]:
        """The file size as one 64-bit value, through ``lpFileSize``."""
        descriptor = self._handle(handle)
        if descriptor is None:
            return 0, 0
        return 1, os.fstat(descriptor).st_size

    def CloseHandle(self, handle: int) -> int:
        descriptor = self._handle(handle)
        if descriptor is None:
            return 0
        os.close(descriptor)
        del self.files[handle]
        return 1

    def GetLastError(self) -> int:
        return self.last_error

    def close(self) -> None:
        for descriptor in self.files.values():
            os.close(descriptor)
        self.files.clear()

    def call(
        self, function: str, signature: Sequence[Any], args: Sequence[Any]
    ) -> tuple[Any, ...]:
        """The result of ``clfn.call_library_function`` for ``function``: the
        return value, then each parameter's value after the call."""
        a = list(args)
        if function == "CreateFileA":
            return (self.CreateFileA(*a), *a)
        if function == "GetLastError":
            return (self.GetLastError(),)
        if function == "SetEndOfFile":
            return (self.SetEndOfFile(a[0]), a[0])
        if function == "GetFileSizeEx":
            ok, size = self.GetFileSizeEx(a[0])
            return ok, a[0], size
        if function == "CloseHandle":
            return self.CloseHandle(a[0]), a[0]
        if function == "GetFileSize":
            low, high = self.GetFileSize(a[0])
            return low, a[0], high
        if function == "SetFilePointer":
            low, high = self.SetFilePointer(a[0], a[1], a[2], a[3])
            return low, a[0], a[1], high, a[3]
        if function == "ReadFile":
            element = signature[2][1]
            result = self.ReadFile(a[0], a[1], a[2], element)
            if result is None:
                return 0, *a
            values, count = result
            return 1, a[0], values, a[2], count, a[4]
        if function == "WriteFile":
            element = signature[2][1]
            written = self.WriteFile(a[0], a[1], a[2], element)
            if written is None:
                return 0, *a
            return 1, a[0], a[1], a[2], written, a[4]
        raise NotImplementedError(f"the kernel32 stand-in has no {function}")


@pytest.fixture
def kernel32(monkeypatch: pytest.MonkeyPatch) -> Kernel32:
    """Route generated calls to kernel32.dll to a ``Kernel32`` stand-in."""
    stand_in = Kernel32()
    real: Callable[..., tuple[Any, ...]] = clfn.call_library_function

    def call(
        library: str, function: str, convention: str, signature: Any, args: Any
    ) -> tuple[Any, ...]:
        if library == "kernel32.dll":
            return stand_in.call(function, signature, args)
        return real(library, function, convention, signature, args)

    monkeypatch.setattr(clfn, "call_library_function", call)
    yield stand_in
    stand_in.close()
