"""Corpus VIs that contain a Call Library Function Node, generated to Python and
RUN on real temporary files, with their outputs asserted.

Every corpus VI that holds a Call Library Function Node, and how it is checked
here:

- Runs here against LabVIEW's own functions (``runtime/lv_manager.py``, no
  library): File Info, LFU Create File Handle, LFU Retrieve File Handle.
- Calls ``kernel32.dll`` (Windows only). OpenG's large-file VIs run end to end
  through ``tests/fake_kernel32.py``, a stand-in built on ordinary file
  descriptors that follows Microsoft's documentation of each function; so does
  Read Discovery Service Key File (success, missing file, failed open). The
  Windows-only calls of Write Environment Variable and Get Client Machine
  Details are checked to reach the library (error 7 here, the real effect on
  Windows).
- Blocked: the 12 protobuf VIs (their SubVI ``Get Server DLL Path.vi`` is not in
  the corpus) and the 3 VIs whose node is inside a Match Regular Expression
  XNode (an XNode has no code generation).
"""

from __future__ import annotations

import ctypes
import getpass
import importlib
import inspect
import os
import socket
import stat
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from lvkit.labview_error import LabVIEWError
from tests.fake_kernel32 import Kernel32

pytestmark = pytest.mark.needs_samples

_SAMPLES = Path(".lvkit/cache/samples")
_OPENG = _SAMPLES / "OpenG/extracted"
_LARGEFILE = _OPENG / "File Group 0/user.lib/_OpenG.lib/largefile/largefile.llb"
_FILE = _OPENG / "File Group 0/user.lib/_OpenG.lib/file/file.llb"
_PLUGIN = _SAMPLES / "measurement-plugin-labview"

# seconds between 1904-01-01 (LabVIEW's reference time) and 1970-01-01
_REFERENCE_TO_UNIX = 2082844800


def _generated_error(module) -> type[Exception]:
    """The ``LabVIEWError`` class of the package a module was generated into."""
    errors = importlib.import_module(module.__name__.split(".")[0] + ".errors")
    return errors.LabVIEWError


def _vi(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"sample corpus VI not present: {path}")
    return path


# ---------------------------------------------------------------- File Info


def test_file_info_reads_a_files_information(generated_vi, tmp_path: Path) -> None:
    module = generated_vi(_vi(_FILE / "File Info__ogtk.vi"), _OPENG)
    target = tmp_path / "data.bin"
    target.write_bytes(b"abcdef")
    os.chmod(target, 0o640)
    os.utime(target, (1_700_000_000, 1_700_000_000))

    result = module.file_info__ogtk(file=target, file_info=SimpleNamespace())

    assert result.file_out == target
    info = result.file_info
    assert info.data_size == 6
    assert info.res_size == 0
    assert info.bfolder is False
    # NI: permissions are "system specific file access rights"; the runtime
    # reports the file's st_mode permission bits, which on Windows only carry
    # the read-only flag.
    assert info.permissions == stat.S_IMODE(target.stat().st_mode)
    assert info.modification_date == 1_700_000_000 + _REFERENCE_TO_UNIX
    assert info.location == (0, 0)
    if sys.platform == "win32":
        # NI documents owner and group of a file with no Windows meaning, and
        # Windows has no POSIX owner or group: the runtime reports them empty.
        assert (info.owner, info.group) == ("", "")
    else:
        assert info.permissions == 0o640
        assert info.owner == target.owner()
        assert info.group == target.group()


def test_file_info_of_a_folder_counts_its_entries(generated_vi, tmp_path: Path) -> None:
    module = generated_vi(_vi(_FILE / "File Info__ogtk.vi"), _OPENG)
    folder = tmp_path / "folder"
    folder.mkdir()
    for name in ("a", "b"):
        (folder / name).write_text(name)

    info = module.file_info__ogtk(file=folder, file_info=SimpleNamespace()).file_info

    assert (info.bfolder, info.data_size) == (True, 2)


def test_file_info_missing_file_is_error_7(generated_vi, tmp_path: Path) -> None:
    module = generated_vi(_vi(_FILE / "File Info__ogtk.vi"), _OPENG)
    with pytest.raises(_generated_error(module)) as raised:
        module.file_info__ogtk(file=tmp_path / "nope", file_info=SimpleNamespace())
    assert raised.value.code == 7


@pytest.mark.skipif(sys.platform == "win32", reason="permissions are POSIX modes")
def test_file_info_update_sets_the_permissions_and_modification_date(
    generated_vi, tmp_path: Path
) -> None:
    module = generated_vi(_vi(_FILE / "File Info__ogtk.vi"), _OPENG)
    target = tmp_path / "data.bin"
    target.write_bytes(b"abcdef")
    os.chmod(target, 0o600)
    info = module.file_info__ogtk(file=target, file_info=SimpleNamespace()).file_info
    info.permissions = 0o640
    info.modification_date = 3_900_000_000

    result = module.file_info__ogtk(file=target, file_info=info, update_f=True)

    assert result.file_out == target
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert int(target.stat().st_mtime) == 3_900_000_000 - _REFERENCE_TO_UNIX


# ------------------------------------------------------- file refnum helpers


def test_file_refnum_round_trips_a_file_handle(generated_vi, tmp_path: Path) -> None:
    create = generated_vi(_vi(_LARGEFILE / "LFU Create File Handle__ogtk.vi"), _OPENG)
    retrieve = generated_vi(
        _vi(_LARGEFILE / "LFU Retrieve File Handle__ogtk.vi"), _OPENG
    )
    first = create.lfu_create_file_handle__ogtk(
        file_path=tmp_path / "a.bin", handle=4660
    ).file_refnum
    second = create.lfu_create_file_handle__ogtk(
        file_path=tmp_path / "b.bin", handle=9
    ).file_refnum

    assert first != second
    assert retrieve.lfu_retrieve_file_handle__ogtk(file_refnum=first).handle_out == 4660
    assert retrieve.lfu_retrieve_file_handle__ogtk(file_refnum=second).handle_out == 9


def test_an_unknown_file_refnum_is_error_1(generated_vi) -> None:
    retrieve = generated_vi(
        _vi(_LARGEFILE / "LFU Retrieve File Handle__ogtk.vi"), _OPENG
    )
    with pytest.raises(_generated_error(retrieve)) as raised:
        retrieve.lfu_retrieve_file_handle__ogtk(file_refnum=987654321)
    assert raised.value.code == 1


# ------------------------------------------------- OpenG large-file VIs (kernel32)

_READ, _WRITE = 1, 2  # LF Open File's open modes: read only, write only


def test_large_file_vis_write_then_read_a_file(
    generated_vi, kernel32: Kernel32, tmp_path: Path
) -> None:
    opener = generated_vi(_vi(_LARGEFILE / "LF Open File__ogtk.vi"), _OPENG)
    writer = generated_vi(_vi(_LARGEFILE / "LF Write File (STR)__ogtk.vi"), _OPENG)
    reader = generated_vi(_vi(_LARGEFILE / "LF Read File (STR)__ogtk.vi"), _OPENG)
    target = tmp_path / "big.bin"
    target.write_bytes(b"")

    out = opener.lf_open_file__ogtk(file_path=target, open_mode_0=_WRITE).refnum_out
    first = writer.lf_write_file_str__ogtk(refnum=out, data="hello ")
    second = writer.lf_write_file_str__ogtk(refnum=first.refnum_out, data="world")
    assert second.offset_out == 11.0
    assert target.read_bytes() == b"hello world"

    handle = opener.lf_open_file__ogtk(file_path=target, open_mode_0=_READ).refnum_out
    read = reader.lf_read_file_str__ogtk(
        refnum=handle, pos_mode_current=0, offset_in_0=6.0, bytes_to_read=5
    )
    assert read.string == "world"
    assert read.offset_out == 11.0


@pytest.mark.parametrize(
    "suffix,code,values",
    [
        ("I16", "<h", [1, -2, 30000]),
        ("I32", "<i", [1, -2, 300000]),
        ("U32", "<I", [1, 4000000000, 7]),
        ("SGL", "<f", [0.5, -2.25, 1024.0]),
        ("DBL", "<d", [0.1, -2.5e10, 3.0]),
    ],
)
def test_large_file_vis_read_and_write_numbers(
    generated_vi,
    kernel32: Kernel32,
    tmp_path: Path,
    suffix: str,
    code: str,
    values: list[float],
) -> None:
    opener = generated_vi(_vi(_LARGEFILE / "LF Open File__ogtk.vi"), _OPENG)
    writer = generated_vi(
        _vi(_LARGEFILE / f"LF Write File ({suffix})__ogtk.vi"), _OPENG
    )
    reader = generated_vi(_vi(_LARGEFILE / f"LF Read File ({suffix})__ogtk.vi"), _OPENG)
    target = tmp_path / "numbers.bin"
    target.write_bytes(b"")
    expected = struct.pack(f"{code[0]}{len(values)}{code[1]}", *values)

    out = opener.lf_open_file__ogtk(file_path=target, open_mode_0=_WRITE).refnum_out
    write = getattr(writer, f"lf_write_file_{suffix.lower()}__ogtk")
    result = write(refnum=out, data=values)
    assert target.read_bytes() == expected
    assert result.offset_out == len(expected)

    handle = opener.lf_open_file__ogtk(file_path=target, open_mode_0=_READ).refnum_out
    read_vi = getattr(reader, f"lf_read_file_{suffix.lower()}__ogtk")
    # the U32 VI names its element count input "count", the others "elements to read"
    count_name = (
        "count"
        if "count" in inspect.signature(read_vi).parameters
        else "elements_to_read"
    )
    read = read_vi(refnum=handle, pos_mode_current=0, **{count_name: len(values)})
    assert read.data == pytest.approx(values)
    assert read.offset_out == len(expected)


def test_file_pointer_size_and_end_of_file_vis(
    generated_vi, kernel32: Kernel32, tmp_path: Path
) -> None:
    opener = generated_vi(_vi(_LARGEFILE / "LF Open File__ogtk.vi"), _OPENG)
    retrieve = generated_vi(
        _vi(_LARGEFILE / "LFU Retrieve File Handle__ogtk.vi"), _OPENG
    )
    pointer = generated_vi(_vi(_LARGEFILE / "LFU Set File Pointer__ogtk.vi"), _OPENG)
    size = generated_vi(_vi(_LARGEFILE / "LFU Get File Size__ogtk.vi"), _OPENG)
    end = generated_vi(_vi(_LARGEFILE / "LFU Set End Of File__ogtk.vi"), _OPENG)
    target = tmp_path / "text.bin"
    target.write_bytes(b"hello world")
    refnum = opener.lf_open_file__ogtk(file_path=target, open_mode_0=_WRITE).refnum_out
    handle = retrieve.lfu_retrieve_file_handle__ogtk(file_refnum=refnum).handle_out

    assert size.lfu_get_file_size__ogtk(handle=handle).file_size == 11.0
    moved = pointer.lfu_set_file_pointer__ogtk(
        handle=handle, pos_mode_current=0, offset_in_0=5.0
    )
    assert moved.offset_out == 5.0
    end.lfu_set_end_of_file__ogtk(handle=handle)
    assert target.read_bytes() == b"hello"
    assert size.lfu_get_file_size__ogtk(handle=handle).file_size == 5.0


def test_error_handler_vi_runs(generated_vi, kernel32: Kernel32) -> None:
    handler = generated_vi(_vi(_LARGEFILE / "LFU Error Handler__ogtk.vi"), _OPENG)
    handler.lfu_error_handler__ogtk(retval=1)  # a succeeded call: nothing to report
    kernel32.last_error = 5
    # a failed call reports error 6
    with pytest.raises(_generated_error(handler)) as raised:
        handler.lfu_error_handler__ogtk(retval=0, prepend_string="test")
    assert raised.value.code == 6
    assert "WinError: test; " in raised.value.source


def test_a_missing_file_fails_to_open(
    generated_vi, kernel32: Kernel32, tmp_path: Path
) -> None:
    opener = generated_vi(_vi(_LARGEFILE / "LF Open File__ogtk.vi"), _OPENG)
    with pytest.raises(_generated_error(opener)):
        opener.lf_open_file__ogtk(file_path=tmp_path / "nope.bin", open_mode_0=_READ)
    assert kernel32.last_error == 2  # ERROR_FILE_NOT_FOUND


# ----------------------------------------------- kernel32 on a Windows machine


@pytest.mark.parametrize(
    "name,value", [("LVKIT_CLFN_TEST", "text"), ("LVKIT_CLFN_EMPTY", "")]
)
def test_write_environment_variable(generated_vi, name: str, value: str) -> None:
    module = generated_vi(
        _vi(
            _PLUGIN
            / "Source/Tests/Tests.Runtime/Measurement Server/Helpers"
            / "Write Environment Variable.vi"
        ),
        _PLUGIN,
    )
    if sys.platform == "win32":
        # SetEnvironmentVariableA changes the process environment block, which
        # os.environ (a snapshot from interpreter start) never sees: read it back
        # through the Windows API, and remove the variable afterwards.
        module.write_environment_variable(name=name, value=value)
        try:
            buffer = ctypes.create_unicode_buffer(256)
            length = ctypes.windll.kernel32.GetEnvironmentVariableW(
                name, buffer, len(buffer)
            )
            assert (buffer.value if length else None) == (value or None)
        finally:
            ctypes.windll.kernel32.SetEnvironmentVariableW(name, None)
    else:
        # kernel32.dll is Windows only: the generated call reaches the library.
        with pytest.raises(LabVIEWError) as raised:
            module.write_environment_variable(name=name, value="x")
        assert (raised.value.code, raised.value.source) == (7, "kernel32.dll")


def test_get_client_machine_details(generated_vi) -> None:
    module = generated_vi(
        _vi(
            _PLUGIN
            / "Source/Runtime/Clients/Session Management V1/Session Reservation"
            / "Get Client Machine Details.vi"
        ),
        _PLUGIN,
    )
    if sys.platform == "win32":
        details = module.get_client_machine_details()
        assert details.hostname.lower() == socket.gethostname().lower()[:15]
        assert details.username == getpass.getuser()
    else:
        with pytest.raises(LabVIEWError) as raised:
            module.get_client_machine_details()
        assert (raised.value.code, raised.value.source) == (7, "kernel32.dll")


_KEY_FILE_VI = (
    _PLUGIN / "Source/Runtime/Clients/Discovery V1/Read Discovery Service Key File.vi"
)


def test_read_discovery_service_key_file(
    generated_vi, kernel32: Kernel32, tmp_path: Path
) -> None:
    module = generated_vi(_vi(_KEY_FILE_VI), _PLUGIN)
    key_file = tmp_path / "key.json"
    key_file.write_text('{"insecurePort": "31763"}')

    result = module.read_discovery_service_key_file(key_file_path=key_file)

    assert result.key_json == '{"insecurePort": "31763"}'
    assert kernel32.files == {}  # the file was closed


def test_read_discovery_service_key_file_of_a_missing_file_clears_error_5054(
    generated_vi, kernel32: Kernel32, tmp_path: Path
) -> None:
    """CreateFileA answers INVALID_HANDLE_VALUE, so GetFileSizeEx fails and the
    VI builds error 5054 through Error Cluster From Error Code. The Clear Errors
    after the read catches the errors of the code before it, so the call returns
    an empty key."""
    module = generated_vi(_vi(_KEY_FILE_VI), _PLUGIN)
    result = module.read_discovery_service_key_file(
        key_file_path=tmp_path / "nope.json"
    )
    assert result.key_json == ""


def test_read_discovery_service_key_file_when_it_cannot_be_opened_is_error_5053(
    generated_vi, kernel32: Kernel32, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CreateFileA answering 0 is the case the VI reports as error 5053."""
    module = generated_vi(_vi(_KEY_FILE_VI), _PLUGIN)
    monkeypatch.setattr(kernel32, "CreateFileA", lambda *args: 0)
    with pytest.raises(_generated_error(module)) as raised:
        module.read_discovery_service_key_file(key_file_path=tmp_path / "key.json")
    assert raised.value.code == 5053
    assert raised.value.message == (
        "Failed to read the discovery service key file.\n"
        "Ensure that the discovery service is running."
    )
