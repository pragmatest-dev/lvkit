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

It replaces ``ctypes.WinDLL`` for ``kernel32.dll`` only: the generated module
gets an object whose functions take the ``ctypes`` arguments the module builds
(``byref`` cells, ``ctypes`` arrays, integers); any other library is the real one.
"""

from __future__ import annotations

import ctypes
import os
from typing import Any

import pytest

GENERIC_WRITE = 0x40000000
CREATE_NEW, CREATE_ALWAYS, OPEN_EXISTING, OPEN_ALWAYS, TRUNCATE_EXISTING = 1, 2, 3, 4, 5
INVALID_HANDLE_VALUE = 0xFFFFFFFF
INVALID_SET_FILE_POINTER = 0xFFFFFFFF
INVALID_FILE_SIZE = 0xFFFFFFFF
ERROR_FILE_NOT_FOUND, ERROR_INVALID_HANDLE, ERROR_FILE_EXISTS = 2, 6, 80


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
        name: Any,
        access: int,
        share: int,
        security: int,
        disposition: int,
        flags: int,
        template: int,
    ) -> int:
        path = os.fsdecode(name.value)
        exists = os.path.exists(path)
        if disposition == CREATE_NEW and exists:
            self.last_error = ERROR_FILE_EXISTS
            return INVALID_HANDLE_VALUE
        if disposition in (OPEN_EXISTING, TRUNCATE_EXISTING) and not exists:
            self.last_error = ERROR_FILE_NOT_FOUND
            return INVALID_HANDLE_VALUE
        mode = os.O_RDWR | os.O_CREAT
        if disposition in (CREATE_ALWAYS, TRUNCATE_EXISTING):
            mode |= os.O_TRUNC
        descriptor = os.open(path, mode)
        self._next += 4
        self.files[self._next] = descriptor
        self.last_error = 0
        return self._next

    def ReadFile(
        self, handle: int, buffer: Any, count: int, read: Any, overlapped: int
    ) -> int:
        descriptor = self._handle(handle)
        if descriptor is None:
            return 0
        data = os.read(descriptor, min(count, ctypes.sizeof(buffer)))
        ctypes.memmove(buffer, data, len(data))
        read._obj.value = len(data)
        return 1

    def WriteFile(
        self, handle: int, buffer: Any, count: int, written: Any, overlapped: int
    ) -> int:
        descriptor = self._handle(handle)
        if descriptor is None:
            return 0
        written._obj.value = os.write(descriptor, bytes(buffer)[:count])
        return 1

    def SetFilePointer(self, handle: int, low: int, high: Any, method: int) -> int:
        descriptor = self._handle(handle)
        if descriptor is None:
            return INVALID_SET_FILE_POINTER
        high_part = high._obj.value if high is not None else 0
        distance = ((high_part & 0xFFFFFFFF) << 32) | (low & 0xFFFFFFFF)
        if distance >= 1 << 63:
            distance -= 1 << 64
        position = os.lseek(
            descriptor, distance, (os.SEEK_SET, os.SEEK_CUR, os.SEEK_END)[method]
        )
        if high is not None:
            high._obj.value = position >> 32
        return position & 0xFFFFFFFF

    def GetFileSize(self, handle: int, high: Any) -> int:
        descriptor = self._handle(handle)
        if descriptor is None:
            return INVALID_FILE_SIZE
        size = os.fstat(descriptor).st_size
        high._obj.value = size >> 32
        return size & 0xFFFFFFFF

    def SetEndOfFile(self, handle: int) -> int:
        descriptor = self._handle(handle)
        if descriptor is None:
            return 0
        os.ftruncate(descriptor, os.lseek(descriptor, 0, os.SEEK_CUR))
        return 1

    def GetFileSizeEx(self, handle: int, size: Any) -> int:
        """The file size as one 64-bit value, through ``lpFileSize``."""
        descriptor = self._handle(handle)
        if descriptor is None:
            return 0
        size._obj.value = os.fstat(descriptor).st_size
        return 1

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

    def __getitem__(self, function: str) -> Any:
        """The function as ``ctypes`` hands it out: an object that takes
        ``argtypes`` and ``restype`` and is called with the arguments."""
        if function.startswith("_") or not callable(getattr(self, function, None)):
            raise AttributeError(f"the kernel32 stand-in has no {function}")
        return _Function(self, function)


class _Function:
    def __init__(self, owner: Kernel32, name: str) -> None:
        self._owner = owner
        self._name = name
        self.argtypes: list[Any] = []
        self.restype: Any = None

    def __call__(self, *args: Any) -> int:
        assert len(args) == len(self.argtypes)
        return getattr(self._owner, self._name)(*args)


@pytest.fixture
def kernel32(monkeypatch: pytest.MonkeyPatch) -> Kernel32:
    """Route ``ctypes.WinDLL('kernel32.dll')`` to a ``Kernel32`` stand-in."""
    stand_in = Kernel32()
    real = getattr(ctypes, "WinDLL", ctypes.CDLL)

    def load(library: str, *args: Any, **kwargs: Any) -> Any:
        if library == "kernel32.dll":
            return stand_in
        return real(library, *args, **kwargs)

    monkeypatch.setattr(ctypes, "WinDLL", load, raising=False)
    yield stand_in
    stand_in.close()
