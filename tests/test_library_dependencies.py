"""The libraries a shared library file needs, read from its headers: PE imports
(and delay-load imports), ELF ``DT_NEEDED`` and Mach-O load commands, told apart by
the file's first bytes."""

from __future__ import annotations

import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.codegen.library_dependencies import (
    LibraryFormat,
    library_format,
    needed_in,
    needed_libraries,
)


def pe_file(imports: list[str], delay_imports: list[str]) -> bytes:
    """A minimal PE32 file: one section holding an import table and a delay-load
    import table naming the given DLLs."""
    section_rva, section_raw = 0x1000, 0x200
    strings = bytearray()
    name_rvas: dict[str, int] = {}
    table_size = 20 * (len(imports) + 1) + 32 * (len(delay_imports) + 1)
    for name in [*imports, *delay_imports]:
        name_rvas[name] = section_rva + table_size + len(strings)
        strings += name.encode() + b"\0"
    table = b"".join(
        struct.pack("<IIIII", 0, 0, 0, name_rvas[n], 0) for n in imports
    ) + bytes(20)
    delay_rva = section_rva + len(table)
    table += b"".join(
        struct.pack("<II", 1, name_rvas[n]) + bytes(24) for n in delay_imports
    ) + bytes(32)
    body = table + bytes(strings)
    optional = bytearray(224)
    struct.pack_into("<H", optional, 0, 0x10B)
    struct.pack_into("<II", optional, 96 + 8, section_rva, 0)  # import directory
    struct.pack_into("<II", optional, 96 + 8 * 13, delay_rva, 0)  # delay-load
    coff = struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, len(optional), 0x2000)
    section = (
        b".idata\0\0"
        + struct.pack("<IIIIIIHHI", len(body), section_rva, len(body), section_raw,
                      0, 0, 0, 0, 0)
    )  # fmt: skip
    header = b"MZ" + bytes(58) + struct.pack("<I", 0x40) + b"PE\0\0" + coff
    header += bytes(optional) + section
    return header.ljust(section_raw, b"\0") + body


def mach_o_file(install_names: list[str], magic: bytes = b"\xce\xfa\xed\xfe") -> bytes:
    """A 32-bit little-endian Mach-O header with one ``LC_LOAD_DYLIB`` per name."""
    commands = b""
    for name in install_names:
        raw = name.encode() + b"\0"
        size = (24 + len(raw) + 3) // 4 * 4
        commands += struct.pack("<IIIIII", 0x0C, size, 24, 0, 0, 0) + raw.ljust(
            size - 24, b"\0"
        )
    header = magic + struct.pack(
        "<IIIIII", 7, 3, 6, len(install_names), len(commands), 0
    )
    return header + commands


def test_pe_imports_and_delay_load_imports_are_listed() -> None:
    data = pe_file(["KERNEL32.dll", "helper.DLL"], ["late.dll"])
    assert library_format(data) is LibraryFormat.PE
    assert needed_in(data) == ["KERNEL32.dll", "helper.DLL", "late.dll"]


def test_a_pe_with_no_imports_needs_nothing() -> None:
    assert needed_in(pe_file([], [])) == []


def test_mach_o_names_beside_the_library_are_listed_and_system_ones_are_not() -> None:
    data = mach_o_file(
        ["@loader_path/libhelper.dylib", "/usr/lib/libSystem.B.dylib",
         "@rpath/libother.dylib"]
    )  # fmt: skip
    assert library_format(data) is LibraryFormat.MACH_O
    assert needed_in(data) == ["libhelper.dylib", "libother.dylib"]


def test_a_fat_mach_o_lists_every_slice() -> None:
    first = mach_o_file(["@loader_path/liba.dylib"])
    second = mach_o_file(["@loader_path/libb.dylib"])
    start = 8 + 2 * 20
    header = b"\xca\xfe\xba\xbe" + struct.pack(">I", 2)
    header += struct.pack(">IIIII", 7, 3, start, len(first), 0)
    header += struct.pack(">IIIII", 7, 3, start + len(first), len(second), 0)
    assert needed_in(header + first + second) == ["liba.dylib", "libb.dylib"]


@pytest.mark.parametrize(
    "data", [b"", b"MZ", b"just text", b"\x7fELF", b"MZ" + bytes(80)]
)
def test_a_file_that_is_not_a_readable_library_needs_nothing(data: bytes) -> None:
    assert needed_in(data) == []


@pytest.mark.skipif(sys.platform == "win32", reason="the libraries are ELF")
def test_elf_needed_entries_are_listed_whatever_the_file_is_called(
    tmp_path: Path,
) -> None:
    compiler = shutil.which("cc") or shutil.which("gcc")
    if compiler is None:
        pytest.skip("no C compiler")
    (tmp_path / "b.c").write_text("int b_value(void) { return 1; }\n")
    (tmp_path / "a.c").write_text("int b_value(void); int a(void){return b_value();}\n")
    subprocess.run(
        [compiler, "-shared", "-fPIC", "-o", str(tmp_path / "libb.so"),
         str(tmp_path / "b.c")],
        check=True,
    )  # fmt: skip
    subprocess.run(
        [compiler, "-shared", "-fPIC", "-o", str(tmp_path / "liba.so"),
         str(tmp_path / "a.c"), f"-L{tmp_path}", "-lb"],
        check=True,
    )  # fmt: skip
    renamed = tmp_path / "liba.dat"
    shutil.copy(tmp_path / "liba.so", renamed)
    assert library_format(renamed.read_bytes()) is LibraryFormat.ELF
    assert "libb.so" in needed_libraries(renamed)
    assert "libb.so" not in needed_libraries(tmp_path / "libb.so")


@pytest.mark.skipif(sys.platform != "win32", reason="reads a Windows system DLL")
def test_kernel32_lists_ntdll() -> None:
    needed = needed_libraries(Path(r"C:\Windows\System32\kernel32.dll"))
    assert "ntdll.dll" in [n.lower() for n in needed]
