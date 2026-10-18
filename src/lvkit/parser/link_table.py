"""Block-diagram link-table decoding: the ``IUVI`` and ``HpVL`` records of
``_LIbd.bin``.

A VI's block-diagram link table (``LIbd``) names, for every node that refers to
another VI, the VI it refers to. One ``IUVI`` record exists per referenced VI
and lists the heap uids of EVERY node that uses it -- a subVI call, or a global
variable node reading/writing a Global VI. pylabview decodes the table into the
main XML for most files (``LIbd//BDHP/IUVI``); for the rest only the raw
``_LIbd.bin`` sidecar is left, and this module reads it.

One ``HpVL`` record exists per Shared Variable the VI uses and lists the heap
uids of EVERY Shared Variable node that reads or writes it. pylabview has no
decoder for it (``LinkObj b'HpVL' parsing not implemented``), so the table
always stays in the sidecar. Layout, verified on one LabVIEW 25.0 VI (a
single-process variable; ``tests/corpus/issues/149``); the head is the one an
``IUVI`` record starts with::

    'HpVL'                                  ident
    <link head>                             name, path, flag, type id, link
                                            reference -- as IUVI above
    u8 len, bytes                           item type (``NI_Variable_Variable``)
    u32 len, bytes                          target (``My Computer``)
    u32 len, bytes                          variable persistent ID (a GUID in
                                            braces; the ``.lvproj`` records the
                                            same ID as ``varPersistentID``)
    u32 count, count x u32                  node uids
    u32 len, bytes                          a second GUID (role not established)
    9 bytes                                 seen as 00 02 00 03 01 00 00 00 01
                                            (role not established)
    u32 len, bytes                          variable type (``Global`` for a
                                            single-process variable, ``Network``
                                            for a network-published one)

The qualified name is the library's name parts followed by the variable's name.
A record of any other kind (``HpVr``, ``VIVl``) is never looked at: the scan
only stops at the idents it knows.

Record layout (pylabview ``LVlinkinfo.py``: ``LinkObjIUseToVILink`` ->
``HeapToVILinkSaveInfo`` -> ``OffsetLinkSaveInfo`` -> ``TypedLinkSaveInfo`` ->
``BasicLinkSaveInfo``). Every offset below counts from the start of the sidecar;
the "pad" steps align to that absolute offset::

    'IUVI'                                  ident
    pad to 4
    u32 count, count x (u8 len, bytes)      qualified name (N parts)
    pad to 2
    path                                    PTH0 / PTH1 / PTH2
    flag                                    u32 (>= 8.6), u8 (>= 8.5)
    type id                                 u16, or u32 when the top bit is set
    VI link reference                       u8 flag byte (>= 14.0.0.3) -- else
                                            u32 + u64 (>= 8.0.0.3), then
                                            u32 + u32 + i32 (>= 6.0.0.1)
    typed-link flags                        u32 (>= 12.0.0.3)
    u32 count, count x u32                  node uids (>= 8.2.0.3)
    path                                    PTH0 / PTH1 / PTH2 (>= 8.2.0.3)
    u8 len, bytes, pad to 2                 iuse string

The layout is version dependent, so the decoder takes the saving VI's version
and rejects a version older than the oldest layout it knows.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from lvkit.models import SharedVariableKind
from lvkit.text_encoding import decode_labview_text

from .utils import clean_labview_string

_IUVI_IDENT = b"IUVI"
_HPVL_IDENT = b"HpVL"
# Every list item in the link table is introduced by this 2-byte "next link"
# marker (pylabview ``LinkObjRefs``); an ident not preceded by it is not a
# record start.
_NEXT_LINK_ITEM = b"\x00\x02"

_MAX_NAME_PARTS = 64
_MAX_UIDS = 1 << 20
_MAX_PATH_COMPONENTS = 64
_MAX_STRING = 1 << 16

_STAGE_BY_NAME = {"development": 1, "alpha": 2, "beta": 3, "release": 4}


class LinkTableError(ValueError):
    """A link-table record that does not follow the documented layout."""


@dataclass(frozen=True, order=True)
class LinkTableVersion:
    """The saving VI's version, as the link-table layout thresholds compare it."""

    major: int
    minor: int
    bugfix: int
    stage: int

    @classmethod
    def from_lvsr(cls, version: ET.Element) -> LinkTableVersion:
        """From the ``<Version>`` element of the main XML's ``LVSR`` section."""
        stage_name = version.get("Stage", "")
        stage = _STAGE_BY_NAME.get(stage_name)
        if stage is None:
            raise LinkTableError(f"unknown version stage {stage_name!r}")
        return cls(
            major=int(version.get("Major", "0")),
            minor=int(version.get("Minor", "0")),
            bugfix=int(version.get("Bugfix", "0")),
            stage=stage,
        )

    def at_least(self, major: int, minor: int, bugfix: int, stage: int) -> bool:
        return self >= LinkTableVersion(major, minor, bugfix, stage)


# The oldest layout this module reads: the heap-to-VI link record introduced
# with 8.2.0.3 (it ends in the uid list and a second path).
_OLDEST_SUPPORTED = (8, 2, 0, 3)


@dataclass(frozen=True)
class IUseLink:
    """One ``IUVI`` record: the VI referenced and every node that uses it."""

    name_parts: tuple[str, ...]
    path_tokens: tuple[str, ...]
    link_flag: int
    node_uids: tuple[int, ...]


@dataclass(frozen=True)
class SharedVariableLink:
    """One ``HpVL`` record: a Shared Variable and every node that uses it.

    ``library_parts`` is the qualified name of the project library the variable
    belongs to (one part per nesting level); ``variable_name`` is the variable's
    own name inside it.
    """

    library_parts: tuple[str, ...]
    variable_name: str
    kind: SharedVariableKind
    persistent_id: str
    item_type: str
    target: str
    node_uids: tuple[int, ...]

    @property
    def library_qualified_name(self) -> str:
        """The library's qualified name, ``a:b`` from its name parts."""
        name = qualified_link_name(list(self.library_parts), 0, None)
        if name is None:
            raise LinkTableError("Shared Variable record names no library")
        return name


@dataclass(frozen=True)
class _LinkHead:
    """What every heap-to-VI link record starts with."""

    name_parts: tuple[str, ...]
    path_tokens: tuple[str, ...]
    link_flag: int


class _Reader:
    """Sequential big-endian reader over the sidecar, tracking the absolute
    offset the alignment steps depend on."""

    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if n < 0 or end > len(self.data):
            raise LinkTableError(f"record runs past the end of the table at {end}")
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def u8(self) -> int:
        return self.take(1)[0]

    def u16(self) -> int:
        return struct.unpack(">H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack(">I", self.take(4))[0]

    def align(self, boundary: int) -> None:
        rem = self.pos % boundary
        if rem:
            self.take(boundary - rem)

    def pascal(self) -> bytes:
        return self.take(self.u8())

    def string32(self) -> str:
        """A string preceded by its u32 length."""
        length = self.u32()
        if length > _MAX_STRING:
            raise LinkTableError(f"string of {length} bytes")
        return decode_labview_text(self.take(length))

    def variable_size_u2p2(self) -> int:
        val = self.u16()
        if val & 0x8000:
            val = ((val & 0x7FFF) << 16) | self.u16()
        return val

    def path(self) -> tuple[str, ...]:
        ident = self.take(4)
        total = self.u32()
        if ident == b"PTH0":
            self.u16()  # path type
            count = self.u16()
            if count > _MAX_PATH_COMPONENTS:
                raise LinkTableError(f"path with {count} components")
            return tuple(decode_labview_text(self.pascal()) for _ in range(count))
        if ident in (b"PTH1", b"PTH2"):
            self.take(4)  # path type ident
            tokens: list[str] = []
            done = 4
            while done < total:
                part = self.take(self.u16())
                tokens.append(decode_labview_text(part))
                done += 2 + len(part)
            return tuple(tokens)
        raise LinkTableError(f"unrecognized path class {ident!r}")


def _read_link_head(r: _Reader, version: LinkTableVersion) -> _LinkHead:
    """The head of a heap-to-VI link record, read from just after its ident."""
    r.align(4)
    count = r.u32()
    if count > _MAX_NAME_PARTS:
        raise LinkTableError(f"qualified name with {count} parts")
    name_parts = tuple(decode_labview_text(r.pascal()) for _ in range(count))
    r.align(2)
    path_tokens = r.path()

    link_flag = 0
    if version.at_least(8, 6, 0, 1):
        link_flag = r.u32()
    elif version.at_least(8, 5, 0, 1):
        link_flag = r.u8()

    r.variable_size_u2p2()  # type id of the link's client type

    flag_byte = 0xFF
    if version.at_least(14, 0, 0, 3):
        flag_byte = r.u8()
    if flag_byte == 0xFF:
        if version.at_least(8, 0, 0, 3):
            r.take(4 + 8)
        if version.at_least(6, 0, 0, 1):
            r.take(4 + 4 + 4)
    return _LinkHead(name_parts, path_tokens, link_flag)


def _read_node_uids(r: _Reader) -> tuple[int, ...]:
    uid_count = r.u32()
    if uid_count > _MAX_UIDS:
        raise LinkTableError(f"record with {uid_count} node uids")
    return tuple(r.u32() for _ in range(uid_count))


def _decode_record(data: bytes, ident_pos: int, version: LinkTableVersion) -> IUseLink:
    r = _Reader(data, ident_pos + len(_IUVI_IDENT))
    head = _read_link_head(r, version)

    if version.at_least(12, 0, 0, 3):
        r.u32()  # typed-link flags
    node_uids = _read_node_uids(r)

    r.path()  # the saved-VI path
    r.pascal()  # the iuse string
    return IUseLink(
        name_parts=head.name_parts,
        path_tokens=head.path_tokens,
        link_flag=head.link_flag,
        node_uids=node_uids,
    )


def _decode_shared_variable_record(
    data: bytes, ident_pos: int, version: LinkTableVersion
) -> SharedVariableLink:
    r = _Reader(data, ident_pos + len(_HPVL_IDENT))
    head = _read_link_head(r, version)
    if len(head.name_parts) < 2:
        raise LinkTableError(
            f"qualified name {head.name_parts!r} lacks a library and a variable"
        )
    item_type = decode_labview_text(r.pascal())
    target = r.string32()
    persistent_id = r.string32()
    node_uids = _read_node_uids(r)
    r.string32()  # second GUID
    r.take(9)
    kind_text = r.string32()
    try:
        kind = SharedVariableKind(kind_text)
    except ValueError:
        raise LinkTableError(f"unknown Shared Variable type {kind_text!r}") from None
    return SharedVariableLink(
        library_parts=head.name_parts[:-1],
        variable_name=head.name_parts[-1],
        kind=kind,
        persistent_id=persistent_id,
        item_type=item_type,
        target=target,
        node_uids=node_uids,
    )


def _record_starts(data: bytes, ident: bytes) -> list[int]:
    """Offsets of every ``ident`` that starts a list item of the table."""
    return [
        match.start()
        for match in re.finditer(re.escape(ident), data)
        if data[max(0, match.start() - 2) : match.start()] == _NEXT_LINK_ITEM
    ]


def _require_supported(version: LinkTableVersion) -> None:
    if not version.at_least(*_OLDEST_SUPPORTED):
        raise LinkTableError(
            f"link table of a {version.major}.{version.minor} VI is older "
            "than the oldest supported layout (8.2)"
        )


def decode_iuse_links(data: bytes, version: LinkTableVersion) -> list[IUseLink]:
    """Every ``IUVI`` record in a ``_LIbd.bin`` sidecar, in file order.

    Raises ``LinkTableError`` when the table holds an ``IUVI`` record but the
    saving VI predates the oldest layout read here, or a record does not follow
    the layout -- an unreadable record is never skipped.
    """
    records: list[IUseLink] = []
    for start in _record_starts(data, _IUVI_IDENT):
        _require_supported(version)
        try:
            records.append(_decode_record(data, start, version))
        except LinkTableError as exc:
            raise LinkTableError(f"IUVI record at offset {start}: {exc}") from exc
    return records


def read_iuse_links(libd_path: Path, version: LinkTableVersion) -> list[IUseLink]:
    """``decode_iuse_links`` over a ``_LIbd.bin`` file."""
    return decode_iuse_links(libd_path.read_bytes(), version)


def decode_shared_variable_links(
    data: bytes, version: LinkTableVersion
) -> list[SharedVariableLink]:
    """Every ``HpVL`` record in a ``_LIbd.bin`` sidecar, in file order.

    Raises ``LinkTableError`` when a record does not follow the documented layout
    -- an unreadable record is never skipped.
    """
    links: list[SharedVariableLink] = []
    for start in _record_starts(data, _HPVL_IDENT):
        _require_supported(version)
        try:
            links.append(_decode_shared_variable_record(data, start, version))
        except LinkTableError as exc:
            raise LinkTableError(f"HpVL record at offset {start}: {exc}") from exc
    return links


def read_shared_variable_links(
    libd_path: Path, version: LinkTableVersion
) -> list[SharedVariableLink]:
    """``decode_shared_variable_links`` over a ``_LIbd.bin`` file."""
    return decode_shared_variable_links(libd_path.read_bytes(), version)


def qualified_link_name(
    parts: list[str],
    link_flag: int,
    caller_library: str | None,
) -> str | None:
    """The qualified name of a link record, ``a:b:c`` from its name parts.

    Control characters and surrounding whitespace are stripped from each part (a
    member name can carry a spurious newline from the binary that would corrupt
    the ``class:member`` identity); parts left empty are dropped. A flag of 2 marks
    a same-library reference: its single part is qualified with the caller's own
    library. ``None`` when no part is left.
    """
    cleaned = [clean_labview_string(p).strip() for p in parts]
    cleaned = [p for p in cleaned if p]
    if not cleaned:
        return None
    if link_flag == 2 and caller_library and len(cleaned) == 1:
        return f"{caller_library}:{cleaned[0]}"
    return ":".join(cleaned)
