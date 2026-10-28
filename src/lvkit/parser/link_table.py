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

A .NET assembly the VI calls has a ``DNDA`` record in ``LIbd`` (every node that
uses it) and a ``DNVA`` record in ``LIvi`` (the VI as a whole). pylabview's
``LinkObjHeapToAssembly`` / ``LinkObjVIToAssembly`` define the layouts; the
assembly's identity is the record's qualified name, four parts::

    'DNVA'                                  ident
    <basic head>                            pad to 4, u32 count, count x (u8 len,
                                            bytes), pad to 2, path, flag (u32
                                            >= 8.6, u8 >= 8.5)
    pad to 2, path                          (>= 10.0.0.1; not read)

    'DNDA'                                  ident
    <link head>                             the head an ``IUVI`` record starts
                                            with
    typed-link flags                        u32 (>= 12.0.0.3)
    u32 count, count x u32                  node uids
    pad to 2, path                          (>= 10.0.0.1; not read)

    qualified name = name, version, culture, public key token
                                            (``MccDaq`` ``3.0.0.0`` ``neutral``
                                            ``a37eefcf5c6ca10a``); the path holds
                                            only the name -- there is no file, the
                                            assembly comes from the GAC

Taken from a reported sample (``DNDA ... 06 "MccDaq" 07 "3.0.0.0" 07 "neutral"
10 "a37eefcf5c6ca10a" PTH0 ... 06 "MccDaq"``) and pylabview's layouts; no
``DNVA`` or ``DNDA`` record of a real file has been decoded here. A record that
does not have those four parts, or that runs past the table, is a
``DotNetAssemblyRecordFailure``.

A malleable VI's instance (the VI file LabVIEW embeds in each caller, see
``lvkit.embedded_instances``) opens its ``LIvi`` table with an ``IVOV``
(instance-to-owner) record; the first ``VIVI`` record after it names the VI the
instance is an instance of. A ``VIVI`` record starts with the same name and path
as an ``IUVI`` record, which is all that is read of it.

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
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from lvkit.dotnet_types import DotNetAssembly
from lvkit.models import SharedVariableKind
from lvkit.text_encoding import decode_labview_text

from .utils import clean_labview_string

_IUVI_IDENT = b"IUVI"
_SVVI_IDENT = b"SVVI"
_SCVI_IDENT = b"SCVI"
_DYOM_IDENT = b"DyOM"
_IVOV_IDENT = b"IVOV"
_VIVI_IDENT = b"VIVI"
_NEXF_IDENT = b"NEXF"
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

    @classmethod
    def of_vi(cls, main_root: ET.Element) -> LinkTableVersion | None:
        """The version a VI was saved in: its save record's (``LVSR``), else its
        version block's (``vers``) when pylabview left the save record raw, as it
        does for LabVIEW 8.2 files. None when the VI records neither."""
        element = main_root.find(".//LVSR/Section/Version")
        if element is None:
            element = main_root.find(".//vers/Section/Version")
        return cls.from_lvsr(element) if element is not None else None

    def at_least(self, major: int, minor: int, bugfix: int, stage: int) -> bool:
        return self >= LinkTableVersion(major, minor, bugfix, stage)


# The oldest layout this module reads: the heap-to-VI link record introduced
# with 8.2.0.3 (it ends in the uid list and a second path).
_OLDEST_SUPPORTED = (8, 2, 0, 3)


@dataclass(frozen=True)
class DynamicDispatchLink:
    """One ``DyOM`` record: the class method a dynamic-dispatch call names (class
    qualified name, then method name) with the path of the class file, and the
    ``dynLink`` uid of every call it belongs to."""

    name_parts: tuple[str, ...]
    path_tokens: tuple[str, ...]
    link_flag: int
    node_uids: tuple[int, ...]


@dataclass(frozen=True)
class ExtFuncLink:
    """One ``NEXF`` record: the library a Call Library Function Node names as
    typed in its setup (``name_parts``, last part), the path LabVIEW recorded for
    it (``path_type`` and ``path_tokens``), the function, and the heap uid of
    every node that calls it."""

    name_parts: tuple[str, ...]
    path_type: int | None
    path_tokens: tuple[str, ...]
    function: str
    node_uids: tuple[int, ...]


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


class DotNetAssemblyRecordKind(str, Enum):
    """The ident of a .NET assembly link record: ``DNDA`` in the block-diagram
    table (with the nodes that use the assembly), ``DNVA`` in the VI table.
    ``(str, Enum)`` so a member equals the ident text."""

    DNDA = "DNDA"
    DNVA = "DNVA"

    @property
    def ident(self) -> bytes:
        return self.value.encode("ascii")


@dataclass(frozen=True)
class DotNetAssemblyLink:
    """One ``DNDA`` or ``DNVA`` record: a .NET assembly the VI uses and, for a
    ``DNDA`` record, the heap uids of every node that uses it."""

    assembly: DotNetAssembly
    node_uids: tuple[int, ...]
    record: DotNetAssemblyRecordKind


@dataclass(frozen=True)
class DotNetAssemblyRecordFailure:
    """One ``DNDA`` or ``DNVA`` record that could not be decoded, and why."""

    reason: str


@dataclass(frozen=True)
class DotNetAssemblyTable:
    """What the ``DNDA`` and ``DNVA`` records of a sidecar hold: the assemblies
    that decoded and the records that did not."""

    links: list[DotNetAssemblyLink]
    failures: list[DotNetAssemblyRecordFailure]


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
    path_type: int | None = None


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
        return self.typed_path()[1]

    def typed_path(self) -> tuple[int | None, tuple[str, ...]]:
        """A path's type (``PTH0`` only: 0 absolute, 1 relative, 2 not a path,
        3 UNC) and its tokens."""
        ident = self.take(4)
        total = self.u32()
        if ident == b"PTH0":
            path_type = self.u16()
            count = self.u16()
            if count > _MAX_PATH_COMPONENTS:
                raise LinkTableError(f"path with {count} components")
            return path_type, tuple(
                decode_labview_text(self.pascal()) for _ in range(count)
            )
        if ident in (b"PTH1", b"PTH2"):
            self.take(4)  # path type ident
            tokens: list[str] = []
            done = 4
            while done < total:
                part = self.take(self.u16())
                tokens.append(decode_labview_text(part))
                done += 2 + len(part)
            return None, tuple(tokens)
        raise LinkTableError(f"unrecognized path class {ident!r}")


def _read_basic_head(r: _Reader, version: LinkTableVersion) -> _LinkHead:
    """The name, path and flag every link record starts with, read from just
    after its ident."""
    r.align(4)
    count = r.u32()
    if count > _MAX_NAME_PARTS:
        raise LinkTableError(f"qualified name with {count} parts")
    name_parts = tuple(decode_labview_text(r.pascal()) for _ in range(count))
    r.align(2)
    path_type, path_tokens = r.typed_path()

    link_flag = 0
    if version.at_least(8, 6, 0, 1):
        link_flag = r.u32()
    elif version.at_least(8, 5, 0, 1):
        link_flag = r.u8()
    return _LinkHead(name_parts, path_tokens, link_flag, path_type)


def _read_link_head(r: _Reader, version: LinkTableVersion) -> _LinkHead:
    """The head of a heap-to-VI link record, read from just after its ident."""
    head = _read_basic_head(r, version)
    if not version.at_least(8, 5, 0, 1):
        r.u32()  # held by every heap-to-VI record of the 29 LabVIEW 8.2 files
    r.variable_size_u2p2()  # type id of the link's client type

    flag_byte = 0xFF
    if version.at_least(14, 0, 0, 3):
        flag_byte = r.u8()
    if flag_byte == 0xFF:
        if version.at_least(8, 0, 0, 3):
            r.take(4 + 8)
        if version.at_least(6, 0, 0, 1):
            r.take(4 + 4 + 4)
    return head


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


_DOTNET_ASSEMBLY_NAME_PARTS = ("name", "version", "culture", "public key token")
_OLDEST_DOTNET_RECORD = (8, 5, 0, 1)


def dotnet_assembly_from_name_parts(parts: Sequence[str]) -> DotNetAssembly:
    """The assembly a ``DNDA`` / ``DNVA`` record's qualified name spells: its
    name, version, culture and public key token, in that order."""
    if len(parts) != len(_DOTNET_ASSEMBLY_NAME_PARTS):
        raise LinkTableError(
            f"qualified name {tuple(parts)!r} has {len(parts)} parts, expected "
            f"{', '.join(_DOTNET_ASSEMBLY_NAME_PARTS)}"
        )
    name, assembly_version, culture, token = parts
    return DotNetAssembly(name, assembly_version, culture, token)


def _decode_dotnet_record(
    data: bytes,
    ident_pos: int,
    record: DotNetAssemblyRecordKind,
    version: LinkTableVersion,
) -> DotNetAssemblyLink:
    if not version.at_least(*_OLDEST_DOTNET_RECORD):
        raise LinkTableError(
            f"{record.value} record of a {version.major}.{version.minor} VI is "
            "older than the oldest supported layout (8.5)"
        )
    r = _Reader(data, ident_pos + len(record.ident))
    node_uids: tuple[int, ...] = ()
    if record is DotNetAssemblyRecordKind.DNDA:
        head = _read_link_head(r, version)
        if version.at_least(12, 0, 0, 3):
            r.u32()  # typed-link flags
        node_uids = _read_node_uids(r)
    else:
        head = _read_basic_head(r, version)
    return DotNetAssemblyLink(
        assembly=dotnet_assembly_from_name_parts(head.name_parts),
        node_uids=node_uids,
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


def _dyn_extra_bytes(version: LinkTableVersion) -> int:
    """Bytes a ``DyOM`` record holds between its 4-aligned end of API content and
    its call uid list that pylabview's layout does not name. Fixed per saving
    version, as measured on the cached sidecars (every record of a version has
    the same size; values vary and their meaning is unknown): none up to 16, 4
    bytes in LabVIEW 17-19, 8 bytes from LabVIEW 20."""
    if version.at_least(20, 0, 0, 0):
        return 8
    if version.at_least(17, 0, 0, 0):
        return 4
    return 0


def _decode_dyn_record(
    data: bytes, ident_pos: int, version: LinkTableVersion
) -> DynamicDispatchLink:
    """A ``DyOM`` record, field by field as pylabview ``parseUDClassHeapAPISaveInfo``
    reads it:

    1. ``BasicLinkSaveInfo``: qualified name, path, flag.
    2. (>= 8.0.0.3) ``UDClassAPILinkCache``, 4-aligned: library version (u64), is
       internal (u8), bool 2 (u8, >= 8.1.0.2), call parent nodes (u8, >= 9.0.0.2),
       API content (u32 length + bytes).
    3. Pad to a 4-byte boundary.
    4. ``_dyn_extra_bytes`` bytes pylabview does not read.
    5. ``LinkOffsetList``: u32 count, then the uids.

    The record must end where the table's next item starts (or at its end); a
    record that does not is an error."""
    r = _Reader(data, ident_pos + len(_DYOM_IDENT))
    head = _read_basic_head(r, version)
    if not version.at_least(8, 0, 0, 3):
        raise LinkTableError("DyOM record of a VI older than 8.0")
    r.align(4)
    r.take(8)  # library version
    r.take(2 if version.at_least(8, 1, 0, 2) else 1)  # is internal (+ bool 2)
    if version.at_least(9, 0, 0, 2):
        r.take(1)  # call parent nodes
    content = r.u32()
    if content > _MAX_STRING:
        raise LinkTableError(f"API content of {content} bytes")
    r.take(content)
    r.align(4)
    r.take(_dyn_extra_bytes(version))
    uids = _read_node_uids(r)
    if data[r.pos : r.pos + 2] != _NEXT_LINK_ITEM and r.pos < len(data) - 2:
        raise LinkTableError("the call uid list does not end the record")
    return DynamicDispatchLink(
        name_parts=head.name_parts,
        path_tokens=head.path_tokens,
        link_flag=head.link_flag,
        node_uids=uids,
    )


def decode_dyn_links(
    data: bytes, version: LinkTableVersion
) -> list[DynamicDispatchLink]:
    """Every ``DyOM`` record in a ``_LIbd.bin`` sidecar, in file order. An
    unreadable record raises ``LinkTableError``, never skipped."""
    records: list[DynamicDispatchLink] = []
    for start in _record_starts(data, _DYOM_IDENT):
        _require_supported(version)
        try:
            records.append(_decode_dyn_record(data, start, version))
        except LinkTableError as exc:
            raise LinkTableError(f"DyOM record at offset {start}: {exc}") from exc
    return records


def read_dyn_links(
    libd_path: Path, version: LinkTableVersion
) -> list[DynamicDispatchLink]:
    """``decode_dyn_links`` over a ``_LIbd.bin`` file."""
    return decode_dyn_links(libd_path.read_bytes(), version)


def decode_iuse_links(data: bytes, version: LinkTableVersion) -> list[IUseLink]:
    """Every ``IUVI``, ``SVVI`` and ``SCVI`` record in a ``_LIbd.bin`` sidecar, in
    file order. An ``SVVI`` record is how a Static VI Reference names the VI it
    refers to, an ``SCVI`` record how a Call By Reference or Start Asynchronous
    Call node whose VI is chosen in the node's setup names it; both have the
    layout of an ``IUVI`` record and list the uid of every such node.

    Raises ``LinkTableError`` when the table holds such a record but the saving VI
    predates the oldest layout read here, or a record does not follow the layout
    -- an unreadable record is never skipped.
    """
    starts = sorted(
        (start, ident)
        for ident in (_IUVI_IDENT, _SVVI_IDENT, _SCVI_IDENT)
        for start in _record_starts(data, ident)
    )
    records: list[IUseLink] = []
    for start, ident in starts:
        _require_supported(version)
        try:
            records.append(_decode_record(data, start, version))
        except LinkTableError as exc:
            raise LinkTableError(
                f"{ident.decode()} record at offset {start}: {exc}"
            ) from exc
    return records


def _decode_ext_func_record(
    data: bytes, ident_pos: int, version: LinkTableVersion
) -> ExtFuncLink:
    """A ``NEXF`` record, field by field as pylabview ``parseExtFuncLinkSaveInfo``
    reads it (LabVIEW 8.0.0.3 and later): the basic link head, the node uid list,
    then the function name as a pascal string padded to an even length."""
    if not version.at_least(8, 0, 0, 3):
        raise LinkTableError("NEXF record of a VI older than 8.0")
    r = _Reader(data, ident_pos + len(_NEXF_IDENT))
    head = _read_basic_head(r, version)
    uids = _read_node_uids(r)
    raw = r.pascal()
    if (len(raw) + 1) % 2:
        r.take(1)
    function = decode_labview_text(raw)
    return ExtFuncLink(
        name_parts=head.name_parts,
        path_type=head.path_type,
        path_tokens=head.path_tokens,
        function=function,
        node_uids=uids,
    )


def decode_ext_func_links(data: bytes, version: LinkTableVersion) -> list[ExtFuncLink]:
    """Every ``NEXF`` record in a ``_LIbd.bin`` sidecar, in file order. An
    unreadable record raises ``LinkTableError``, never skipped."""
    records: list[ExtFuncLink] = []
    for start in _record_starts(data, _NEXF_IDENT):
        _require_supported(version)
        try:
            records.append(_decode_ext_func_record(data, start, version))
        except LinkTableError as exc:
            raise LinkTableError(f"NEXF record at offset {start}: {exc}") from exc
    return records


@dataclass(frozen=True)
class InstanceSourceLink:
    """The VI a malleable-VI instance is an instance of: its qualified name parts
    and recorded path (relative to the instance)."""

    name_parts: tuple[str, ...]
    path_tokens: tuple[str, ...]


def decode_instance_source(
    data: bytes, version: LinkTableVersion
) -> InstanceSourceLink | None:
    """The VI an instance is an instance of, from the instance's raw ``_LIvi.bin``
    sidecar: the first ``VIVI`` record after its ``IVOV`` record. ``None`` when
    the table has no ``IVOV`` record (the VI is not an instance) or no ``VIVI``
    record after it."""
    owner_starts = _record_starts(data, _IVOV_IDENT)
    if not owner_starts:
        return None
    source_starts = [
        start for start in _record_starts(data, _VIVI_IDENT) if start > owner_starts[0]
    ]
    if not source_starts:
        return None
    try:
        head = _read_basic_head(_Reader(data, source_starts[0] + 4), version)
    except LinkTableError as exc:
        raise LinkTableError(
            f"VIVI record at offset {source_starts[0]}: {exc}"
        ) from exc
    return InstanceSourceLink(head.name_parts, head.path_tokens)


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


def decode_dotnet_assemblies(
    data: bytes, version: LinkTableVersion
) -> DotNetAssemblyTable:
    """Every ``DNDA`` and ``DNVA`` record in a ``_LIbd.bin`` / ``_LIvi.bin``
    sidecar, in file order: the assemblies that decoded, and a
    ``DotNetAssemblyRecordFailure`` for each record that did not follow the
    documented layout -- an unreadable record is reported, never skipped."""
    table = DotNetAssemblyTable(links=[], failures=[])
    starts = sorted(
        (start, record)
        for record in DotNetAssemblyRecordKind
        for start in _record_starts(data, record.ident)
    )
    for start, record in starts:
        try:
            table.links.append(_decode_dotnet_record(data, start, record, version))
        except LinkTableError as exc:
            table.failures.append(
                DotNetAssemblyRecordFailure(
                    f"{record.value} record at offset {start}: {exc}"
                )
            )
    return table


def read_dotnet_assemblies(
    sidecar_path: Path, version: LinkTableVersion
) -> DotNetAssemblyTable:
    """``decode_dotnet_assemblies`` over a link-table sidecar file."""
    return decode_dotnet_assemblies(sidecar_path.read_bytes(), version)


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
