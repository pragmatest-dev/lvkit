"""LabVIEW's file manager functions as Python (runtime/lv_manager.py), and the
Call Library Function Nodes that call them by the library name LabVIEW."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

from lvkit.manager_functions import manager_functions
from lvkit.runtime import lv_manager

# 1904-01-01 to 1970-01-01, in seconds.
_REFERENCE_TO_UNIX = 2082844800


def test_new_ref_num_makes_a_refnum_that_gives_the_file_descriptor_back() -> None:
    error, path, fd, refnum = lv_manager.new_ref_num("a.bin", 42, 0)
    assert (error, path, fd) == (0, "a.bin", 42)
    assert refnum != 0
    assert lv_manager.ref_num_to_fd(refnum, 0) == (0, refnum, 42)
    other = lv_manager.new_ref_num("b.bin", 43, 0)[3]
    assert other != refnum
    assert lv_manager.ref_num_to_fd(other, 0)[2] == 43


def test_ref_num_to_fd_of_an_unknown_refnum_is_mg_arg_err_and_null() -> None:
    assert lv_manager.ref_num_to_fd(987654, 7) == (lv_manager.MG_ARG_ERR, 987654, 0)


def test_get_info_fills_the_552_byte_record_of_a_file(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_bytes(b"x" * 1234)
    os.chmod(target, 0o640)
    os.utime(target, (1_700_000_000, 1_700_000_000))
    error, path, record = lv_manager.get_info(target, [0] * 552)
    assert (error, path) == (0, target)
    assert len(record) == lv_manager.RECORD_SIZE == 552
    raw = bytes(record)
    # type, creator, permissions, size, rfSize, cdate, mdate, folder, isInvisible,
    # location (vertical, horizontal)
    fields = struct.unpack_from("=iiiiiIIiihh", raw)
    assert fields[0:2] == (0, 0)
    assert fields[2] == (0o640 if sys.platform != "win32" else fields[2])
    assert fields[3:5] == (1234, 0)
    assert fields[6] == 1_700_000_000 + _REFERENCE_TO_UNIX
    assert fields[7:11] == (0, 0, 0, 0)
    # the owner and group are Pascal strings in 256 bytes each
    owner_length, group_length = raw[40], raw[296]
    assert owner_length <= 255 and group_length <= 255
    assert raw[41 + owner_length : 296] == bytes(255 - owner_length)


def test_get_info_of_a_directory_counts_its_entries(tmp_path: Path) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / name).write_text(name)
    _, _, record = lv_manager.get_info(tmp_path, [0] * 552)
    fields = struct.unpack_from("=iiiiiIIiihh", bytes(record))
    assert fields[3] == 3  # entries
    assert fields[7] == 1  # folder


def test_get_info_errors_leave_the_buffer_as_it_was(tmp_path: Path) -> None:
    missing = lv_manager.get_info(tmp_path / "nope", [9] * 552)
    assert missing == (lv_manager.F_NOT_FOUND, tmp_path / "nope", [9] * 552)
    small = lv_manager.get_info(tmp_path, [9] * 10)
    assert small == (lv_manager.MG_ARG_ERR, tmp_path, [9] * 10)


@pytest.mark.skipif(sys.platform == "win32", reason="permissions are POSIX modes")
def test_set_info_sets_the_permissions_and_modification_date(tmp_path: Path) -> None:
    target = tmp_path / "data.bin"
    target.write_text("x")
    os.chmod(target, 0o600)
    _, _, record = lv_manager.get_info(target, [0] * 552)
    head = list(struct.unpack_from("=iiiiiIIiihh", bytes(record)))
    head[2] = 0o644
    head[6] = 1_600_000_000 + _REFERENCE_TO_UNIX
    record[:40] = struct.pack("=iiiiiIIiihh", *head)
    assert lv_manager.set_info(target, record)[0] == lv_manager.NO_ERR
    status = target.stat()
    assert status.st_mode & 0o777 == 0o644
    assert int(status.st_mtime) == 1_600_000_000


def test_set_info_of_a_missing_file_changes_nothing(tmp_path: Path) -> None:
    result = lv_manager.set_info(tmp_path / "nope", [0] * 552)
    assert result[0] == lv_manager.F_NOT_FOUND


def test_every_function_in_the_table_is_a_runtime_function() -> None:
    import importlib

    assert set(manager_functions()) == {
        "FNewRefNum",
        "FRefNumToFD",
        "FGetInfo",
        "FSetInfo",
    }
    for function in manager_functions().values():
        module_alias, attribute = function.python.split(".")
        assert callable(
            getattr(importlib.import_module("lvkit.runtime.lv_manager"), attribute)
        )
        assert module_alias == "_lv_manager"
        assert function.import_statement == (
            "from lvkit.runtime import lv_manager as _lv_manager"
        )
        assert function.doc_url.startswith("https://docs-be.ni.com/")
