"""Decode a bottom-up TypeDesc pool into named ``LVType``s.

Pre-LV9 (LV8.x) VIs have no ``VCTP`` consolidated type pool, and pylabview leaves
the blocks that hold their types raw. Two of those blocks carry a TypeDesc pool in
the SAME bottom-up format VCTP uses, and both decode here:

* ``CONP`` -- the connector-pane terminal types (see ``conp_types``).
* ``DTHP`` -- the block-diagram / front-panel heap types (see ``heap_types``).

Binary layout of a pool::

    u32  count
    count x TypeDesc:
        u16  total_len      (includes these 2 bytes)
        u8   flags          (0x40 = the body ends in a Pascal-string label)
        u8   type_code      (LabVIEW TD_FULL_TYPE)
        ...type-specific body...
        [label]             (a Pascal string, present exactly when flag 0x40 is set)

TypeDescs are bottom-up: a cluster/array/refnum names its members by INDEX into
the TDs decoded before it. A typedef (``0xf1``) instead embeds its inner TypeDesc
directly after its qualified name.

The type-specific bodies follow pylabview's own readers (``LVdatatype`` and
``LVdatatyperef``). A descriptor that does not decode is not dropped: its entry is
a type that says why it is unreadable.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from pathlib import Path

from ..lv_type_codes import REFNUM_TYPE, TD_FULL_TYPE
from ..models import (
    ClusterField,
    EnumValue,
    LVType,
    LVTypeKind,
    enum_values_from_labels,
)
from .naming import build_qualified_name

# Scalars whose descriptor has no body before the label.
_NO_BODY_TYPES = frozenset(
    {
        TD_FULL_TYPE.NumInt8,
        TD_FULL_TYPE.NumInt16,
        TD_FULL_TYPE.NumInt32,
        TD_FULL_TYPE.NumInt64,
        TD_FULL_TYPE.NumUInt8,
        TD_FULL_TYPE.NumUInt16,
        TD_FULL_TYPE.NumUInt32,
        TD_FULL_TYPE.NumUInt64,
        TD_FULL_TYPE.NumFloat32,
        TD_FULL_TYPE.NumFloat64,
        TD_FULL_TYPE.NumFloatExt,
        TD_FULL_TYPE.NumComplex64,
        TD_FULL_TYPE.NumComplex128,
        TD_FULL_TYPE.NumComplexExt,
        TD_FULL_TYPE.BooleanU16,
        TD_FULL_TYPE.Boolean,
        TD_FULL_TYPE.CString,
        TD_FULL_TYPE.PasString,
        TD_FULL_TYPE.LVVariant,
    }
)
# Scalars whose body is a ``u32`` before the label (the size of the blob).
_BLOB_TYPES = frozenset(
    {
        TD_FULL_TYPE.String,
        TD_FULL_TYPE.Path,
        TD_FULL_TYPE.Picture,
        TD_FULL_TYPE.SubString,
    }
)
# Enum types: the width of the ordinal they hold.
_ENUM_TYPES = frozenset(
    {TD_FULL_TYPE.UnitUInt8, TD_FULL_TYPE.UnitUInt16, TD_FULL_TYPE.UnitUInt32}
)

# Refnum kinds whose body is the kind, then a count and that many client TD
# indexes (a parametrized refnum carrying one element/payload type).
_CLIENT_LIST_REFNUMS = frozenset(
    {
        REFNUM_TYPE.DataLog,
        REFNUM_TYPE.NotifierRef,
        REFNUM_TYPE.Queue,
        REFNUM_TYPE.UserEvent,
        REFNUM_TYPE.DataValueRef,
        REFNUM_TYPE.FIFORef,
    }
)
# A data value reference's client list is followed by an "is external" byte.
_EXTERNAL_FLAG_REFNUMS = frozenset({REFNUM_TYPE.DataValueRef})
# Refnum kinds whose body is the kind alone.
_KIND_ONLY_REFNUMS = frozenset(
    {
        REFNUM_TYPE.Generic,
        REFNUM_TYPE.ByteStream,
        REFNUM_TYPE.Device,
        REFNUM_TYPE.Occurrence,
        REFNUM_TYPE.TCPNetConn,
        REFNUM_TYPE.Menu,
        REFNUM_TYPE.DataSocket,
        REFNUM_TYPE.UDPNetConn,
        REFNUM_TYPE.IrdaNetConn,
        REFNUM_TYPE.BluetoothCon,
        REFNUM_TYPE.TDMSFile,
    }
)

# The 4-byte field a class refnum's body carries after its 2-byte field. A pool
# block exists only in files older than LabVIEW 8.6, and pylabview reads this
# field in every file older than 8.6.1.
_CLASS_REFNUM_OLD_FIELD_BYTES = 4


def read_var_size(data: bytes | memoryview, pos: int) -> tuple[int, int] | None:
    """A 16-bit value, or a 32-bit one when the first bit is set (the high bit is
    not part of the value). Returns ``(value, next position)``."""
    if pos + 2 > len(data):
        return None
    value = int.from_bytes(data[pos : pos + 2], "big")
    if value & 0x8000:
        if pos + 4 > len(data):
            return None
        return ((value & 0x7FFF) << 16) | int.from_bytes(
            data[pos + 2 : pos + 4], "big"
        ), pos + 4
    return value, pos + 2


class PoolError(Exception):
    """A descriptor that does not follow its type's layout; the message says how."""


@dataclass(frozen=True)
class RawTD:
    """One TypeDesc as stored: its type code, flags and undecoded body."""

    type_code: int
    flags: int
    body: bytes

    @property
    def has_label(self) -> bool:
        return bool(self.flags & 0x40)


@dataclass(frozen=True)
class TdList:
    """The TypeDescs a pool block lists, the offset just past the last one read,
    and why reading stopped early (None when every listed descriptor was read)."""

    tds: list[RawTD]
    end: int
    failure: str | None


def read_td_list(data: bytes, start: int = 0) -> TdList:
    """Split ``u32 count`` + the TypeDescs at ``start``."""
    if start + 4 > len(data):
        return TdList([], start, "the block is too short to hold a descriptor count")
    (count,) = struct.unpack_from(">I", data, start)
    off = start + 4
    tds: list[RawTD] = []
    for index in range(count):
        if off + 4 > len(data):
            return TdList(tds, off, f"the block ends inside descriptor {index}")
        (tlen,) = struct.unpack_from(">H", data, off)
        if tlen < 4 or off + tlen > len(data):
            return TdList(
                tds,
                off,
                f"descriptor {index} has a length of {tlen} bytes it cannot hold",
            )
        tds.append(RawTD(data[off + 3], data[off + 2], data[off + 4 : off + tlen]))
        off += tlen
    return TdList(tds, off, None)


def unreadable_type(reason: str) -> LVType:
    """The type of a descriptor that did not decode, carrying why."""
    return LVType(kind=LVTypeKind.PRIMITIVE, unreadable=reason)


def _pstr(data: bytes, off: int) -> tuple[str, int]:
    """Read a length-prefixed (1-byte) latin-1 string at ``off``; return it and
    the offset past it."""
    if off >= len(data):
        raise PoolError("a string runs past the end of the descriptor")
    n = data[off]
    if off + 1 + n > len(data):
        raise PoolError("a string runs past the end of the descriptor")
    return data[off + 1 : off + 1 + n].decode("latin1"), off + 1 + n


def _decode_enum(body: bytes, start: int) -> tuple[dict[str, EnumValue], int]:
    """Enum body at ``start``: ``u16 count`` then ``count`` label pstrs, padded to
    an even offset (relative to the body). Returns the ordinal-valued members and
    the offset past the padding, where the terminal label pstr starts."""
    (count,) = struct.unpack_from(">H", body, start)
    off = start + 2
    labels: list[str] = []
    for _ in range(count):
        label, off = _pstr(body, off)
        labels.append(label)
    return enum_values_from_labels(labels), off + (off & 1)


def _label(td: RawTD, off: int) -> str | None:
    """The descriptor's label, which starts at ``off`` exactly when its flags say
    it has one."""
    if not td.has_label:
        return None
    label, _ = _pstr(td.body, off)
    return label or None


class TdPool:
    """A decoded TypeDesc pool: per-TD ``LVType`` and label, in pool order. A
    descriptor that did not decode has an ``unreadable_type`` and its reason in
    ``failures``."""

    def __init__(self, tds: list[RawTD]):
        self.tds = tds
        self.lvtypes: list[LVType] = []
        self.names: list[str | None] = []
        self.failures: dict[int, str] = {}
        # The strictly bottom-up order is what lets a member ref resolve against
        # TDs already decoded.
        for index, td in enumerate(tds):
            try:
                lv, name = self._decode(td)
            except (PoolError, struct.error, IndexError) as exc:
                reason = (
                    str(exc)
                    if isinstance(exc, PoolError)
                    else "its body is shorter than its type's layout"
                )
                self.failures[index] = reason
                lv, name = unreadable_type(reason), None
            self.lvtypes.append(lv)
            self.names.append(name)

    def _ref(self, index: int) -> LVType:
        if index >= len(self.lvtypes):
            raise PoolError(f"it refers to descriptor {index}, which is not listed")
        return self.lvtypes[index]

    def _decode(self, td: RawTD) -> tuple[LVType, str | None]:
        code = td.type_code
        body = td.body
        try:
            full_type = TD_FULL_TYPE(code)
        except ValueError:
            raise PoolError(f"type code 0x{code:02X} is not a known type") from None

        if full_type == TD_FULL_TYPE.Void:
            return LVType(
                kind=LVTypeKind.PRIMITIVE, underlying_type=full_type.name
            ), _label(td, 0)

        if full_type in _NO_BODY_TYPES:
            return LVType(
                kind=LVTypeKind.PRIMITIVE, underlying_type=full_type.name
            ), _label(td, 0)

        if full_type in _BLOB_TYPES:
            if len(body) < 4:
                raise PoolError("its body is shorter than its type's layout")
            return LVType(
                kind=LVTypeKind.PRIMITIVE, underlying_type=full_type.name
            ), _label(td, 4)

        if full_type in _ENUM_TYPES:
            values, off = _decode_enum(body, 0)
            return LVType(
                kind=LVTypeKind.ENUM,
                underlying_type=full_type.name,
                values=values,
            ), _label(td, off)

        if full_type == TD_FULL_TYPE.Cluster:
            return self._decode_cluster(td)

        if full_type == TD_FULL_TYPE.Array:
            return self._decode_array(td)

        if full_type == TD_FULL_TYPE.Refnum:
            return self._decode_refnum(td)

        if full_type == TD_FULL_TYPE.TypeDef:
            return self._decode_typedef(td)

        raise PoolError(f"type code 0x{code:02X} ({full_type.name}) is not read")

    def _decode_array(self, td: RawTD) -> tuple[LVType, str | None]:
        """``u16`` ndims, ndims x ``u32`` dimension size, then the element TD."""
        body = td.body
        (ndims,) = struct.unpack_from(">H", body, 0)
        ref_off = 2 + 4 * ndims
        element = read_var_size(body, ref_off)
        if element is None:
            raise PoolError("its body is shorter than its type's layout")
        elem_ref, label_off = element
        return LVType(
            kind=LVTypeKind.ARRAY,
            underlying_type=TD_FULL_TYPE.Array.name,
            element_type=self._ref(elem_ref),
            dimensions=ndims or 1,
        ), _label(td, label_off)

    def _decode_cluster(self, td: RawTD) -> tuple[LVType, str | None]:
        body = td.body
        counted = read_var_size(body, 0)
        if counted is None:
            raise PoolError("its body is shorter than its type's layout")
        fcount, pos = counted
        refs: list[int] = []
        for _ in range(fcount):
            member = read_var_size(body, pos)
            if member is None:
                raise PoolError("its body is shorter than its type's layout")
            refs.append(member[0])
            pos = member[1]
        member_types = [self._ref(r) for r in refs]
        fields = [
            ClusterField(name=self.names[r] or f"field_{i}", type=member_types[i])
            for i, r in enumerate(refs)
        ]
        return LVType(
            kind=LVTypeKind.CLUSTER,
            underlying_type=TD_FULL_TYPE.Cluster.name,
            fields=fields,
        ), _label(td, pos)

    def _decode_refnum(self, td: RawTD) -> tuple[LVType, str | None]:
        """``u16`` refnum kind, then a kind-specific part (pylabview's
        ``LVdatatyperef``). A parametrized refnum carries its element TD index; a
        class refnum carries its class name; a control reference carries a
        qualified name when it has one."""
        body = td.body
        (kind_code,) = struct.unpack_from(">H", body, 0)
        try:
            kind = REFNUM_TYPE(kind_code)
        except ValueError:
            raise PoolError(f"refnum kind {kind_code} is not a known kind") from None

        element_type: LVType | None = None
        user_event_name: str | None = None
        classname: str | None = None
        class_file: str | None = None
        label_off: int | None

        if kind in _CLIENT_LIST_REFNUMS:
            (nclients,) = struct.unpack_from(">H", body, 2)
            pos = 4
            clients: list[int] = []
            for _ in range(nclients):
                client = read_var_size(body, pos)
                if client is None:
                    raise PoolError("its body is shorter than its type's layout")
                clients.append(client[0])
                pos = client[1]
            if nclients == 1:
                element_type = self._ref(clients[0])
                if kind == REFNUM_TYPE.UserEvent:
                    user_event_name = self.names[clients[0]]
            if kind in _EXTERNAL_FLAG_REFNUMS:
                pos += 1
            label_off = pos
        elif kind == REFNUM_TYPE.UDClassInst:
            items, label_off = _read_class_items(body)
            classname = ":".join(items)
            class_file = items[-1] if items else None
        elif kind == REFNUM_TYPE.LVObjCtl:
            label_off = _control_reference_end(body)
        elif kind in _KIND_ONLY_REFNUMS:
            label_off = 2
        else:
            # The layout of this kind's body is not read, so neither is its label.
            label_off = None

        name = _label(td, label_off) if label_off is not None else None
        return LVType(
            kind=LVTypeKind.PRIMITIVE,
            underlying_type=TD_FULL_TYPE.Refnum.name,
            ref_type=kind.name,
            classname=classname,
            class_file=class_file,
            element_type=element_type,
            user_event_name=user_event_name,
        ), name

    def _decode_typedef(self, td: RawTD) -> tuple[LVType, str | None]:
        """``u32`` flag, ``u32`` count + that many qualified-name pstrs (owning
        libraries, then the ``.ctl``), then the inner TypeDesc as a nested
        ``[u16 len][flags][type][body]`` record running to the end of the body.
        The typedef takes the inner type's structure, labelled by the inner
        descriptor's own label."""
        body = td.body
        (nnames,) = struct.unpack_from(">I", body, 4)
        off = 8
        names: list[str] = []
        for _ in range(nnames):
            s, off = _pstr(body, off)
            names.append(s)
        if not names:
            raise PoolError("it names no file")
        if td.has_label:
            raise PoolError("a type definition with a label of its own is not read")
        if off + 4 > len(body):
            raise PoolError("its body is shorter than its type's layout")
        inner = RawTD(body[off + 3], body[off + 2], body[off + 4 :])
        lv, label = self._decode(inner)
        return replace(
            lv,
            typedef_name=build_qualified_name(names[:-1], names[-1]),
            typedef_file=names[-1],
        ), label


def _read_class_items(body: bytes) -> tuple[list[str], int]:
    """A class refnum's class name parts, and the offset just past them: after the
    2-byte kind, a 2-byte field and the old 4-byte field, a length byte covers the
    names. A name that ends in a zero byte is a list of length-prefixed parts
    ended by a zero length; otherwise the bytes are one name. The whole is padded
    to an even size."""
    pos = 2 + 2 + _CLASS_REFNUM_OLD_FIELD_BYTES
    if pos >= len(body):
        raise PoolError("its body is shorter than its type's layout")
    total = body[pos]
    start = pos + 1
    end = start + total
    if end > len(body):
        raise PoolError("its class name runs past the end of the descriptor")
    text = body[start:end]
    items: list[str] = []
    if text and text[-1] == 0:
        cursor = 0
        while cursor < len(text):
            n = text[cursor]
            if n == 0:
                break
            if cursor + 1 + n > len(text):
                raise PoolError("its class name parts run past the name")
            items.append(text[cursor + 1 : cursor + 1 + n].decode("latin1"))
            cursor += 1 + n
    else:
        items.append(text.decode("latin1"))
    return items, end + ((total + 1) % 2)


def _control_reference_end(body: bytes) -> int:
    """Where a control reference's body ends: the kind, a client count and that
    many client indexes, a flags word, a has-item word and, when it has an item,
    its 4-byte identifier and a qualified name (a ``u32`` count of strings)."""
    (nclients,) = struct.unpack_from(">H", body, 2)
    pos = 4
    for _ in range(nclients):
        client = read_var_size(body, pos)
        if client is None:
            raise PoolError("its body is shorter than its type's layout")
        pos = client[1]
    pos += 2
    (has_item,) = struct.unpack_from(">H", body, pos)
    pos += 2
    if has_item:
        pos += 4
        (count,) = struct.unpack_from(">I", body, pos)
        pos += 4
        for _ in range(count):
            _, pos = _pstr(body, pos)
    return pos


def block_sidecar_path(xml_path: Path | str, block: str) -> Path:
    """The ``*_<block>.bin`` sidecar beside a VI's extracted XML, derived from ANY
    of its sibling XMLs -- the main ``<stem>.xml`` or a ``<stem>_FPHb.xml`` /
    ``_BDHb.xml`` heap. One place so every caller of a raw-block sidecar agrees on
    the path."""
    p = Path(xml_path)
    stem = p.name
    for suffix in ("_FPHb.xml", "_BDHb.xml", ".xml"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return p.with_name(f"{stem}_{block}.bin")
