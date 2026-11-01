"""LabVIEW's file manager functions written out as Python by the Call Library
Function Nodes that call them by the library name LabVIEW (``data/
labview_manager.json``), run on real temporary files."""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

from lvkit.manager_functions import manager_functions
from lvkit.parser.extfunc_prototype import ExtFuncParam, ParamKind
from tests.ext_func_vi import build_vi

# 1904-01-01 to 1970-01-01, in seconds.
_REFERENCE_TO_UNIX = 2082844800
_NUMBER = ExtFuncParam(ParamKind.VALUE, "int32")
_PATH = ExtFuncParam(ParamKind.ADAPT_TO_TYPE)
_RECORD = ExtFuncParam(ParamKind.ARRAY_DATA, "uint8")


def _file_info():  # noqa: ANN202
    return build_vi(
        "LabVIEW",
        "FGetInfo",
        [_NUMBER, _PATH, _RECORD],
        ["NumInt32", "Path", "Array"],
        controls={1: "file", 2: "record"},
        indicators={0: "error", 2: "info"},
    )


def test_get_info_is_os_stat_into_the_552_byte_record(tmp_path: Path) -> None:
    clfn_test, source = _file_info()
    assert "lvkit" not in source
    assert "_status = os.stat(file)" in source
    target = tmp_path / "data.bin"
    target.write_bytes(b"x" * 1234)
    os.chmod(target, 0o640)
    os.utime(target, (1_700_000_000, 1_700_000_000))

    result = clfn_test(str(target), [0] * 552)

    assert result.error == 0
    assert len(result.info) == 552
    raw = bytes(result.info)
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
    if sys.platform != "win32":
        assert raw[41 : 41 + owner_length].decode() == target.owner()
        assert raw[297 : 297 + group_length].decode() == target.group()
    assert raw[41 + owner_length : 296] == bytes(255 - owner_length)


def test_get_info_of_a_directory_counts_its_entries(tmp_path: Path) -> None:
    clfn_test, _ = _file_info()
    for name in ("a", "b", "c"):
        (tmp_path / name).write_text(name)
    fields = struct.unpack_from(
        "=iiiiiIIiihh", bytes(clfn_test(str(tmp_path), [0] * 552).info)
    )
    assert fields[3] == 3  # entries
    assert fields[7] == 1  # folder


def test_get_info_of_a_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    clfn_test, _ = _file_info()
    with pytest.raises(FileNotFoundError):
        clfn_test(str(tmp_path / "nope"), [0] * 552)


@pytest.mark.skipif(sys.platform == "win32", reason="permissions are POSIX modes")
def test_set_info_is_chmod_and_utime_from_the_record(tmp_path: Path) -> None:
    get, _ = _file_info()
    set_info, source = build_vi(
        "LabVIEW",
        "FSetInfo",
        [_NUMBER, _PATH, _RECORD],
        ["NumInt32", "Path", "Array"],
        controls={1: "file", 2: "record"},
        indicators={0: "error"},
    )
    assert "os.chmod(file, _record.permissions & 4095)" in source
    target = tmp_path / "data.bin"
    target.write_text("x")
    os.chmod(target, 0o600)
    record = get(str(target), [0] * 552).info
    head = list(struct.unpack_from("=iiiiiIIiihh", bytes(record)))
    head[2] = 0o644
    head[6] = 1_600_000_000 + _REFERENCE_TO_UNIX
    record[:40] = struct.pack("=iiiiiIIiihh", *head)

    assert set_info(str(target), record).error == 0

    status = target.stat()
    assert status.st_mode & 0o777 == 0o644
    assert int(status.st_mtime) == 1_600_000_000


def test_set_info_of_a_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    set_info, _ = build_vi(
        "LabVIEW",
        "FSetInfo",
        [_NUMBER, _PATH, _RECORD],
        ["NumInt32", "Path", "Array"],
        controls={1: "file", 2: "record"},
        indicators={0: "error"},
    )
    with pytest.raises(FileNotFoundError):
        set_info(str(tmp_path / "nope"), [0] * 552)


def test_the_refnum_is_the_file_descriptor() -> None:
    create, source = build_vi(
        "LabVIEW",
        "FNewRefNum",
        [
            _NUMBER,
            _PATH,
            ExtFuncParam(ParamKind.VALUE, "uint32"),
            ExtFuncParam(ParamKind.POINTER, "uint32"),
        ],
        ["NumInt32", "Path", "NumUInt32", "NumUInt32"],
        controls={1: "file", 2: "fd"},
        indicators={0: "error", 3: "refnum"},
    )
    retrieve, _ = build_vi(
        "LabVIEW",
        "FRefNumToFD",
        [
            _NUMBER,
            ExtFuncParam(ParamKind.VALUE, "uint32"),
            ExtFuncParam(ParamKind.POINTER, "uint32"),
        ],
        ["NumInt32", "NumUInt32", "NumUInt32"],
        controls={1: "refnum"},
        indicators={0: "error", 2: "fd"},
    )
    assert "p3_out = fd" in source
    made = create("a.bin", 4660)
    assert (made.error, made.refnum) == (0, 4660)
    assert retrieve(made.refnum).fd == 4660


def test_every_function_in_the_table_has_a_template() -> None:
    assert set(manager_functions()) == {
        "FNewRefNum",
        "FRefNumToFD",
        "FGetInfo",
        "FSetInfo",
    }
    for function in manager_functions().values():
        assert function.statements
        assert function.doc_url.startswith("https://docs-be.ni.com/")
