"""The libraries a shared library file needs, read from its headers.

The format is told by the file's first bytes, not its extension:

* PE (a Windows DLL): the import table and the delay-load import table name the
  DLLs the file links against.
* ELF (a Linux ``.so``): the ``DT_NEEDED`` entries of the dynamic section.
* Mach-O (a macOS library, thin or fat): the ``LC_LOAD_DYLIB``-family load
  commands; a name under ``@loader_path/`` or ``@rpath/`` is reduced to its file
  name, an absolute install name is a system library and is not listed.

Only names are returned, in the order the file lists them. A file of another
format (or one whose headers do not read) needs nothing here.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath


class LibraryFormat(str, Enum):
    PE = "pe"
    ELF = "elf"
    MACH_O = "mach-o"


_MACH_O_MAGICS = {
    b"\xfe\xed\xfa\xce": (">", False),
    b"\xfe\xed\xfa\xcf": (">", True),
    b"\xce\xfa\xed\xfe": ("<", False),
    b"\xcf\xfa\xed\xfe": ("<", True),
}
_FAT_MAGIC = b"\xca\xfe\xba\xbe"
# Load commands that name a library the file links against.
_LC_LOAD_DYLIB = 0x0C
_LC_LOAD_WEAK_DYLIB = 0x80000018
_LC_REEXPORT_DYLIB = 0x8000001F
_LC_LAZY_LOAD_DYLIB = 0x20
_LC_LOAD_UPWARD_DYLIB = 0x80000023
_DYLIB_COMMANDS = {
    _LC_LOAD_DYLIB,
    _LC_LOAD_WEAK_DYLIB,
    _LC_REEXPORT_DYLIB,
    _LC_LAZY_LOAD_DYLIB,
    _LC_LOAD_UPWARD_DYLIB,
}
_MACH_O_RELATIVE_PREFIXES = ("@loader_path/", "@rpath/")


def library_format(data: bytes) -> LibraryFormat | None:
    """The format the first bytes of ``data`` announce."""
    if data[:4] == b"\x7fELF":
        return LibraryFormat.ELF
    if data[:2] == b"MZ" and len(data) >= 0x40:
        offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[offset : offset + 4] == b"PE\0\0":
            return LibraryFormat.PE
    if data[:4] in _MACH_O_MAGICS or data[:4] == _FAT_MAGIC:
        return LibraryFormat.MACH_O
    return None


def needed_libraries(path: Path) -> list[str]:
    """The file names of the libraries ``path`` needs, from its headers."""
    return needed_in(path.read_bytes())


def needed_in(data: bytes) -> list[str]:
    """``needed_libraries`` over the file's bytes."""
    found = library_format(data)
    try:
        if found is LibraryFormat.PE:
            return _pe_imports(data)
        if found is LibraryFormat.ELF:
            return _elf_needed(data)
        if found is LibraryFormat.MACH_O:
            return _mach_o_needed(data)
    except (struct.error, IndexError, ValueError):
        return []
    return []


def _c_string(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode("utf-8", errors="replace")


@dataclass(frozen=True)
class _Section:
    virtual_address: int
    virtual_size: int
    raw_offset: int


def _pe_imports(data: bytes) -> list[str]:
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    section_count = struct.unpack_from("<H", data, pe + 6)[0]
    optional_size = struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    directories = optional + (96 if magic == 0x10B else 112)
    sections = []
    table = optional + optional_size
    for index in range(section_count):
        base = table + 40 * index
        size, address, raw_size, raw = struct.unpack_from("<IIII", data, base + 8)
        sections.append(_Section(address, max(size, raw_size), raw))

    def offset_of(rva: int) -> int:
        for section in sections:
            if (
                section.virtual_address
                <= rva
                < (section.virtual_address + section.virtual_size)
            ):
                return rva - section.virtual_address + section.raw_offset
        raise ValueError(f"RVA {rva:#x} is in no section")

    names: list[str] = []

    def table_at(directory: int, size: int, name_field: int) -> None:
        rva, _ = struct.unpack_from("<II", data, directories + 8 * directory)
        if not rva:
            return
        position = offset_of(rva)
        while True:
            entry = data[position : position + size]
            if len(entry) < size or not any(entry):
                return
            name_rva = struct.unpack_from("<I", entry, name_field)[0]
            names.append(_c_string(data, offset_of(name_rva)))
            position += size

    table_at(1, 20, 12)  # import directory: Name
    table_at(13, 32, 4)  # delay-load import directory: DllNameRVA
    return names


def _elf_needed(data: bytes) -> list[str]:
    wide = data[4] == 2
    fmt = "<" if data[5] == 1 else ">"
    if wide:
        (phoff,) = struct.unpack_from(fmt + "Q", data, 0x20)
        phentsize, phnum = struct.unpack_from(fmt + "HH", data, 0x36)
    else:
        (phoff,) = struct.unpack_from(fmt + "I", data, 0x1C)
        phentsize, phnum = struct.unpack_from(fmt + "HH", data, 0x2A)
    loads: list[tuple[int, int, int]] = []  # (virtual address, file offset, size)
    dynamic: tuple[int, int] | None = None
    for index in range(phnum):
        base = phoff + phentsize * index
        if wide:
            kind, _, offset, address, _, filesz, _, _ = struct.unpack_from(
                fmt + "IIQQQQQQ", data, base
            )
        else:
            kind, offset, address, _, filesz, _, _, _ = struct.unpack_from(
                fmt + "IIIIIIII", data, base
            )
        if kind == 1:
            loads.append((address, offset, filesz))
        elif kind == 2:
            dynamic = (offset, filesz)
    if dynamic is None:
        return []
    entry = 16 if wide else 8
    word = fmt + ("qQ" if wide else "iI")
    needed: list[int] = []
    string_table = 0
    for position in range(dynamic[0], dynamic[0] + dynamic[1], entry):
        tag, value = struct.unpack_from(word, data, position)
        if tag == 0:
            break
        if tag == 1:
            needed.append(value)
        elif tag == 5:
            string_table = value
    for address, offset, size in loads:
        if address <= string_table < address + size:
            base = string_table - address + offset
            return [_c_string(data, base + n) for n in needed]
    return []


def _mach_o_needed(data: bytes) -> list[str]:
    if data[:4] == _FAT_MAGIC:
        count = struct.unpack_from(">I", data, 4)[0]
        names: list[str] = []
        for index in range(count):
            offset, size = struct.unpack_from(">II", data, 8 + 20 * index + 8)
            names += _mach_o_slice(data[offset : offset + size])
        return names
    return _mach_o_slice(data)


def _mach_o_slice(data: bytes) -> list[str]:
    order, wide = _MACH_O_MAGICS[data[:4]]
    count = struct.unpack_from(order + "I", data, 16)[0]
    position = 32 if wide else 28
    names: list[str] = []
    for _ in range(count):
        command, size = struct.unpack_from(order + "II", data, position)
        if command in _DYLIB_COMMANDS:
            name_offset = struct.unpack_from(order + "I", data, position + 8)[0]
            install_name = _c_string(data, position + name_offset)
            for prefix in _MACH_O_RELATIVE_PREFIXES:
                if install_name.startswith(prefix):
                    names.append(PurePosixPath(install_name).name)
        position += size
    return names
