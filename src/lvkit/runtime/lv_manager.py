"""Python equivalents of the LabVIEW file manager functions that Call Library
Function Nodes call by the library name ``LabVIEW``.

NI's pages for each (labview-api-ref, "LabVIEW Manager Function"): FNewRefNum,
FRefNumToFD, FGetInfo, FSetInfo. Each takes the input value of every parameter
row, in NI's order, and returns the function's ``MgErr`` followed by each
parameter's value after the call -- the contract of ``clfn.call_library_function``.

``MgErr`` values used (NI's lists for these functions): ``noErr`` 0, ``mgArgErr``
1, ``fIOErr`` 6, ``fNotFound`` 7.
"""

from __future__ import annotations

import datetime
import itertools
import os
import stat
import struct
from collections.abc import Sequence
from typing import Any

NO_ERR = 0
MG_ARG_ERR = 1
F_IO_ERR = 6
F_NOT_FOUND = 7

# File refnums: NI says a refnum "contains a file descriptor ... and additional
# information used by LabVIEW" and its structure is private, so here a refnum is
# a number naming a (path, file descriptor) pair.
_refnums: dict[int, tuple[Any, Any]] = {}
_next_refnum = itertools.count(1)


def new_ref_num(path: Any, fd: Any, ref_num_ptr: Any) -> tuple[Any, ...]:
    """FNewRefNum(path, fd, refNumPtr): a new file refnum for the open file
    ``fd`` at ``path``; it is the value ``refNumPtr`` ends up holding."""
    refnum = next(_next_refnum)
    _refnums[refnum] = (path, fd)
    return NO_ERR, path, fd, refnum


def ref_num_to_fd(ref_num: Any, fdp: Any) -> tuple[Any, ...]:
    """FRefNumToFD(refNum, fdp): the file descriptor of ``refNum``. For a refnum
    that names no open file, NULL (0) is stored and ``mgArgErr`` returned."""
    entry = _refnums.get(ref_num)
    if entry is None:
        return MG_ARG_ERR, ref_num, 0
    return NO_ERR, ref_num, entry[1]


# FInfoRec, from NI's FGetInfo page, laid out in 4-byte fields: int32 type,
# creator, permissions, size, rfSize; uint32 cdate, mdate; Bool32 folder,
# isInvisible; Point location (two int16, vertical then horizontal); and the Str255
# owner and group (a length byte, then up to 255 characters, in 256 bytes). The
# offsets are the ones OpenG's File Info VI reads from the buffer it passes.
_RECORD_HEAD = struct.Struct("=iiiiiIIiihh")
_STR255 = 256
_OWNER_OFFSET = _RECORD_HEAD.size
_GROUP_OFFSET = _OWNER_OFFSET + _STR255
RECORD_SIZE = _GROUP_OFFSET + _STR255

# NI: "creation date: seconds since system reference time". LabVIEW's reference
# time is 1904-01-01 UTC (its time stamps count from it, and OpenG's saved File
# Info value, a creation date of 3188231670, falls in 2005 only by that count).
_REFERENCE_TIME = datetime.datetime(1904, 1, 1, tzinfo=datetime.timezone.utc)
_UNIX_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
_REFERENCE_TO_UNIX = int((_UNIX_EPOCH - _REFERENCE_TIME).total_seconds())
_INT32_MAX = 2**31 - 1


def _pascal(text: str) -> bytes:
    data = text.encode("latin-1", "replace")[: _STR255 - 1]
    return (bytes([len(data)]) + data).ljust(_STR255, b"\0")


def _owner_and_group(status: os.stat_result) -> tuple[str, str]:
    try:
        import grp
        import pwd

        return pwd.getpwuid(status.st_uid).pw_name, grp.getgrgid(status.st_gid).gr_name
    except (ImportError, KeyError):
        return "", ""


def _file_info_record(path: str) -> bytes:
    """The FInfoRec of ``path``. ``type`` and ``creator`` are system specific and
    0 here (NI gives 0 for directories and, on the Mac only, for folders);
    ``size`` is the byte count of a file (at most the largest int32) or the
    number of entries in a directory."""
    status = os.stat(path)
    folder = stat.S_ISDIR(status.st_mode)
    size = len(os.listdir(path)) if folder else min(status.st_size, _INT32_MAX)
    created = getattr(status, "st_birthtime", status.st_ctime)
    owner, group = _owner_and_group(status)
    head = _RECORD_HEAD.pack(
        0,
        0,
        stat.S_IMODE(status.st_mode),
        size,
        0,
        int(created) + _REFERENCE_TO_UNIX,
        int(status.st_mtime) + _REFERENCE_TO_UNIX,
        int(folder),
        0,
        0,
        0,
    )
    return head + _pascal(owner) + _pascal(group)


def get_info(path: Any, infop: Sequence[int]) -> tuple[Any, ...]:
    """FGetInfo(path, infop): fill the FInfoRec ``infop`` with information about
    the file or directory at ``path``. ``fNotFound`` when there is none and
    ``mgArgErr`` for a buffer too small to hold the record; ``infop`` is left as
    it was on an error."""
    buffer = list(infop)
    if len(buffer) < RECORD_SIZE:
        return MG_ARG_ERR, path, buffer
    try:
        record = _file_info_record(os.fspath(path))
    except FileNotFoundError:
        return F_NOT_FOUND, path, buffer
    except OSError:
        return F_IO_ERR, path, buffer
    buffer[:RECORD_SIZE] = record
    return NO_ERR, path, buffer


def set_info(path: Any, infop: Sequence[int]) -> tuple[Any, ...]:
    """FSetInfo(path, infop): set the permissions and the modification date of
    the file or directory at ``path`` from the FInfoRec ``infop``. (The other
    attributes are not something a file system lets a caller set.) Nothing
    changes on an error."""
    buffer = list(infop)
    if len(buffer) < RECORD_SIZE:
        return MG_ARG_ERR, path, buffer
    fields = _RECORD_HEAD.unpack(bytes(buffer[: _RECORD_HEAD.size]))
    permissions, modified = fields[2] & 0o7777, fields[6] - _REFERENCE_TO_UNIX
    target = os.fspath(path)
    try:
        before = os.stat(target)
    except FileNotFoundError:
        return F_NOT_FOUND, path, buffer
    except OSError:
        return F_IO_ERR, path, buffer
    try:
        os.chmod(target, permissions)
        os.utime(target, (before.st_atime, modified))
    except OSError:
        os.chmod(target, stat.S_IMODE(before.st_mode))
        return F_IO_ERR, path, buffer
    return NO_ERR, path, buffer
