r"""Block-diagram link-table decoding: the ``IUVI`` and ``HpVL`` records of
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
always stays in the sidecar. Layout, verified on two LabVIEW 25.0 VIs (a
single-process variable, ``tests/corpus/issues/149``, and the same variable
published to the network, ``tests/corpus/issues/149/network``); the head is the
one an ``IUVI`` record starts with::

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
    u16, u16                                role not established; the first
                                            differs from record to record, the
                                            second is the same for every record
                                            of a VI

    then one of two layouts, each validated by the type string it ends with. The
    single-process layout is read first and kept only if its type reads ``Global``;
    otherwise the network-published layout is read and kept only if its type reads
    ``Network``:

    single process:
    u8, u32                                 the u8 seen as 00 and 01, the u32 seen
                                            as 1 (roles not established)
    u32 len, bytes                          variable type, ``Global``

    network-published:
    u32 len, bytes                          network path (``\\.\SV Sample\Value``;
                                            ``\\.\`` is the local machine)
    4 x u32                                 seen as ffffffff 1 2 1 (roles not
                                            established)
    u32 len, bytes                          variable type, ``Network``

The qualified name is the library's name parts followed by the variable's name.

An ``HpVr`` record is how a node refers to a variable that was already
network-published when the node was placed. It has the same layout as ``HpVL``
and is decoded by the same code, with one difference in the name: the record's
own target (the ``My Computer`` field above) is the first name part, so it is
dropped before the library and variable are split. A first part that is not the
record's target is a ``SharedVariableRecordFailure``. This layout is taken from
the reporter's description of the record and the structure it shares with
``HpVL``; it has not been checked against a real ``HpVr`` file.

A record that fits neither layout is reported as a
``SharedVariableRecordFailure``, never guessed at. A record of any other kind
(``VIVl``) is never looked at: the scan only stops at the idents it knows.

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
from enum import Enum
from pathlib import Path

from lvkit.models import SharedVariableKind
from lvkit.text_encoding import decode_labview_text

from .utils import clean_labview_string

_IUVI_IDENT = b"IUVI"
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


class SharedVariableRecordKind(str, Enum):
    """The ident of the link-table record a Shared Variable link was read from,
    which is how the node refers to the variable: ``HpVL``, or ``HpVr`` (seen for
    nodes placed while the variable was already network-published).
    ``(str, Enum)`` so a member equals the ident text."""

    HPVL = "HpVL"
    HPVR = "HpVr"

    @property
    def ident(self) -> bytes:
        return self.value.encode("ascii")


@dataclass(frozen=True)
class SharedVariableLink:
    r"""One ``HpVL`` or ``HpVr`` record: a Shared Variable and every node that
    uses it.

    ``library_parts`` is the qualified name of the project library the variable
    belongs to (one part per nesting level); ``variable_name`` is the variable's
    own name inside it. ``network_path`` is the path a network-published variable
    is published at (``\\.\SV Sample\Value``), ``None`` for a single-process one.

    ``second_guid``, ``second_guid_values``, ``layout_byte`` and ``layout_values``
    are recorded as read, their role not established: the second GUID, the two
    ``u16`` values after it, the byte that opens a single-process layout (``None``
    for a network-published one), and the ``u32`` values that follow (one for a
    single-process record, four for a network-published one).
    """

    library_parts: tuple[str, ...]
    variable_name: str
    kind: SharedVariableKind
    persistent_id: str
    item_type: str
    target: str
    node_uids: tuple[int, ...]
    network_path: str | None = None
    second_guid: str = ""
    second_guid_values: tuple[int, int] = (0, 0)
    layout_byte: int | None = None
    layout_values: tuple[int, ...] = ()
    record: SharedVariableRecordKind = SharedVariableRecordKind.HPVL

    @property
    def library_qualified_name(self) -> str:
        """The library's qualified name, ``a:b`` from its name parts."""
        name = qualified_link_name(list(self.library_parts), 0, None)
        if name is None:
            raise LinkTableError("Shared Variable record names no library")
        return name


@dataclass(frozen=True)
class SharedVariableRecordFailure:
    """One ``HpVL`` or ``HpVr`` record that could not be decoded: where it starts
    in the sidecar, why, and the heap uids of the nodes it lists when the failure came
    after the uid list was read (``None`` when it came before)."""

    offset: int
    reason: str
    node_uids: tuple[int, ...] | None


@dataclass(frozen=True)
class SharedVariableTable:
    """What the ``HpVL`` and ``HpVr`` records of a sidecar hold: the links that
    decoded and the records that did not."""

    links: list[SharedVariableLink]
    failures: list[SharedVariableRecordFailure]


class _SharedVariableRecordError(LinkTableError):
    """A malformed ``HpVL`` or ``HpVr`` record, with the node uids read before the
    failure."""

    def __init__(self, message: str, node_uids: tuple[int, ...] | None) -> None:
        super().__init__(message)
        self.node_uids = node_uids


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


_SV_NETWORK_VALUES = 4


@dataclass(frozen=True)
class _SharedVariableLayout:
    """What follows the two ``u16`` values of a Shared Variable record."""

    kind: SharedVariableKind
    layout_byte: int | None
    network_path: str | None
    layout_values: tuple[int, ...]


def _read_single_process_layout(r: _Reader) -> _SharedVariableLayout:
    layout_byte = r.u8()
    layout_values = (r.u32(),)
    kind_text = r.string32()
    if kind_text != SharedVariableKind.SINGLE_PROCESS.value:
        raise LinkTableError(f"type {kind_text!r} where 'Global' was expected")
    return _SharedVariableLayout(
        SharedVariableKind.SINGLE_PROCESS, layout_byte, None, layout_values
    )


def _read_network_layout(r: _Reader) -> _SharedVariableLayout:
    network_path = r.string32()
    layout_values = tuple(r.u32() for _ in range(_SV_NETWORK_VALUES))
    kind_text = r.string32()
    if kind_text != SharedVariableKind.NETWORK.value:
        raise LinkTableError(f"type {kind_text!r} where 'Network' was expected")
    return _SharedVariableLayout(
        SharedVariableKind.NETWORK, None, network_path, layout_values
    )


def _read_shared_variable_layout(data: bytes, pos: int) -> _SharedVariableLayout:
    """The layout that ends the record at ``pos``: the single-process one when its
    type string reads ``Global``, else the network-published one when its type
    string reads ``Network``; a record that fits neither is an error."""
    try:
        return _read_single_process_layout(_Reader(data, pos))
    except LinkTableError as single_error:
        try:
            return _read_network_layout(_Reader(data, pos))
        except LinkTableError as network_error:
            raise LinkTableError(
                "fits neither Shared Variable layout "
                f"(single process: {single_error}; "
                f"network-published: {network_error})"
            ) from None


def _decode_shared_variable_record(
    data: bytes,
    ident_pos: int,
    record: SharedVariableRecordKind,
    version: LinkTableVersion,
) -> SharedVariableLink:
    r = _Reader(data, ident_pos + len(record.ident))
    node_uids: tuple[int, ...] | None = None
    try:
        head = _read_link_head(r, version)
        item_type = decode_labview_text(r.pascal())
        target = r.string32()
        persistent_id = r.string32()
        node_uids = _read_node_uids(r)
        name_parts = head.name_parts
        if record is SharedVariableRecordKind.HPVR:
            if not name_parts or name_parts[0] != target:
                raise LinkTableError(
                    f"qualified name {name_parts!r} does not start with the "
                    f"record's target {target!r}"
                )
            name_parts = name_parts[1:]
        if len(name_parts) < 2:
            raise LinkTableError(
                f"qualified name {name_parts!r} lacks a library and a variable"
            )
        second_guid = r.string32()
        second_guid_values = (r.u16(), r.u16())
        layout = _read_shared_variable_layout(data, r.pos)
    except LinkTableError as exc:
        raise _SharedVariableRecordError(str(exc), node_uids) from exc
    return SharedVariableLink(
        library_parts=name_parts[:-1],
        variable_name=name_parts[-1],
        kind=layout.kind,
        persistent_id=persistent_id,
        item_type=item_type,
        target=target,
        node_uids=node_uids,
        network_path=layout.network_path,
        second_guid=second_guid,
        second_guid_values=second_guid_values,
        layout_byte=layout.layout_byte,
        layout_values=layout.layout_values,
        record=record,
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
) -> SharedVariableTable:
    """Every ``HpVL`` and ``HpVr`` record in a ``_LIbd.bin`` sidecar, in file
    order: the links that decoded, and a ``SharedVariableRecordFailure`` for each
    record that did not follow the documented layout -- an unreadable record is
    reported, never skipped and never guessed at."""
    table = SharedVariableTable(links=[], failures=[])
    starts = sorted(
        (start, record)
        for record in SharedVariableRecordKind
        for start in _record_starts(data, record.ident)
    )
    for start, record in starts:
        try:
            _require_supported(version)
            table.links.append(
                _decode_shared_variable_record(data, start, record, version)
            )
        except LinkTableError as exc:
            table.failures.append(
                SharedVariableRecordFailure(
                    offset=start,
                    reason=f"{record.value} record at offset {start}: {exc}",
                    node_uids=(
                        exc.node_uids
                        if isinstance(exc, _SharedVariableRecordError)
                        else None
                    ),
                )
            )
    return table


def read_shared_variable_links(
    libd_path: Path, version: LinkTableVersion
) -> SharedVariableTable:
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
