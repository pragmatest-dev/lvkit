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

TypeDescs are bottom-up: a cluster/array/refnum names its members by INDEX into
the TDs decoded before it. A typedef (``0xf1``) instead embeds its inner TypeDesc
directly after its qualified name.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from pathlib import Path

from ..models import (
    ClusterField,
    EnumValue,
    LVType,
    LVTypeKind,
    enum_values_from_labels,
)
from .naming import build_qualified_name

# TD_FULL_TYPE code -> the ``underlying_type`` string ``LVType.type_descriptor``
# expects, so a pool-decoded scalar renders identically to a VCTP-decoded one.
_SCALAR_CODE: dict[int, str] = {
    0x01: "NumInt8",
    0x02: "NumInt16",
    0x03: "NumInt32",
    0x04: "NumInt64",
    0x05: "NumUInt8",
    0x06: "NumUInt16",
    0x07: "NumUInt32",
    0x08: "NumUInt64",
    0x09: "NumFloat32",
    0x0A: "NumFloat64",
    0x0B: "NumFloatExt",
    0x0C: "NumComplex64",
    0x0D: "NumComplex128",
    0x0E: "NumComplexExt",
    0x20: "BooleanU16",
    0x21: "Boolean",
    0x30: "String",
    0x31: "String2",
    0x32: "Path",
    0x33: "Picture",
    0x34: "CString",
    0x35: "PasString",
    0x37: "Tag",
    0x3F: "SubString",
    0x53: "LVVariant",
}
# Scalar codes whose body starts with a ``u32`` before the label pstr.
_U32_PREFIXED = frozenset({0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x37, 0x3F})
# Enum codes -> their TD_FULL_TYPE name (the ordinal's width).
_ENUM_CODES: dict[int, str] = {
    0x15: "UnitUInt8",
    0x16: "UnitUInt16",
    0x17: "UnitUInt32",
}

# Refnum sub-kind code (u16 at the start of a 0x70 body) -> the RefType name
# LabVIEW uses, so a pool-decoded refnum renders identically -- e.g.
# ``Queue refnum``, ``LVObjCtl refnum`` -- to a VCTP-decoded one.
_REFNUM_KIND: dict[int, str] = {
    0: "Generic",
    1: "DataLog",
    2: "ByteStream",
    3: "Device",
    4: "Occurrence",
    5: "TCPNetConn",
    7: "AutoRef",
    8: "LVObjCtl",
    9: "Menu",
    11: "Imaq",
    13: "DataSocket",
    14: "VisaRef",
    15: "IVIRef",
    16: "UDPNetConn",
    17: "NotifierRef",
    18: "Queue",
    19: "IrdaNetConn",
    20: "UsrDefined",
    21: "UsrDefndTag",
    23: "EventReg",
    24: "DotNet",
    25: "UserEvent",
    27: "Callback",
    29: "UsrDefTagFlt",
    30: "UDClassInst",
    31: "BluetoothCon",
    32: "DataValueRef",
    33: "FIFORef",
    34: "TDMSFile",
}
# Refnum kinds whose body is ``u16 count`` + ``count x u16`` client TD index: a
# parametrized refnum carrying one element/payload type.
_PARAMETRIZED_REFNUMS = frozenset({1, 17, 18, 25, 32, 33})

_VOID, _ARRAY, _CLUSTER, _REFNUM = 0x00, 0x40, 0x50, 0x70
_CONPANE, _TYPEDEF = 0xF0, 0xF1


@dataclass(frozen=True)
class RawTD:
    """One TypeDesc as stored: its type code, flags and undecoded body."""

    type_code: int
    flags: int
    body: bytes

    @property
    def has_label(self) -> bool:
        return bool(self.flags & 0x40)


def read_td_list(data: bytes, start: int = 0) -> tuple[list[RawTD], int]:
    """Split ``u32 count`` + the TypeDescs at ``start``. Returns them and the
    offset just past the last one. Stops early at a structural surprise."""
    if start + 4 > len(data):
        return [], start
    (count,) = struct.unpack_from(">I", data, start)
    off = start + 4
    tds: list[RawTD] = []
    for _ in range(count):
        if off + 4 > len(data):
            break
        (tlen,) = struct.unpack_from(">H", data, off)
        if tlen < 4 or off + tlen > len(data):
            break
        tds.append(RawTD(data[off + 3], data[off + 2], data[off + 4 : off + tlen]))
        off += tlen
    return tds, off


def _pstr(data: bytes, off: int) -> tuple[str, int]:
    """Read a length-prefixed (1-byte) latin-1 string at ``off``; return it and
    the offset past it."""
    n = data[off]
    return data[off + 1 : off + 1 + n].decode("latin1"), off + 1 + n


def _is_type_identity(s: str | None) -> bool:
    """A ``.lvclass``/``.ctl`` string is a TYPE identity, never a terminal label."""
    return bool(s) and (".lvclass" in s or ".ctl" in s)  # type: ignore[arg-type]


def _all_pstrs(body: bytes) -> list[str]:
    """Every printable length-prefixed string in ``body`` (best-effort; used for
    the name-bearing refnum bodies whose fixed layout is only partly known)."""
    out: list[str] = []
    off = 0
    while off < len(body):
        n = body[off]
        if n == 0 or off + 1 + n > len(body):
            off += 1
            continue
        chunk = body[off + 1 : off + 1 + n]
        if all(32 <= c < 127 for c in chunk):
            out.append(chunk.decode("latin1"))
            off += 1 + n
        else:
            off += 1
    return out


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


def _name_at(body: bytes, off: int) -> str | None:
    if 0 <= off < len(body):
        try:
            s, _ = _pstr(body, off)
            return s or None
        except IndexError:
            return None
    return None


class TdPool:
    """A decoded TypeDesc pool: per-TD ``LVType`` and label, in pool order."""

    def __init__(self, tds: list[RawTD]):
        self.tds = tds
        self.lvtypes: list[LVType | None] = []
        self.names: list[str | None] = []
        # The strictly bottom-up order is what lets a member ref resolve against
        # TDs already decoded.
        for td in tds:
            lv, name = self._decode(td.type_code, td.body)
            self.lvtypes.append(lv)
            self.names.append(name)

    def _ref(self, index: int) -> LVType | None:
        return self.lvtypes[index] if index < len(self.lvtypes) else None

    def _decode(
        self,
        type_code: int,
        body: bytes,
    ) -> tuple[LVType | None, str | None]:
        try:
            if type_code == _VOID:
                return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Void"), None

            if type_code in _SCALAR_CODE:
                name_off = 4 if type_code in _U32_PREFIXED else 0
                return LVType(
                    kind=LVTypeKind.PRIMITIVE,
                    underlying_type=_SCALAR_CODE[type_code],
                ), _name_at(body, name_off)

            if type_code in _ENUM_CODES:
                values, off = _decode_enum(body, 0)
                return LVType(
                    kind=LVTypeKind.ENUM,
                    underlying_type=_ENUM_CODES[type_code],
                    values=values,
                ), _name_at(body, off)

            if type_code == _CLUSTER:
                return self._decode_cluster(body)

            if type_code == _ARRAY:
                # u16 ndims, ndims x u32 dimension size, u16 element TD ref.
                (ndims,) = struct.unpack_from(">H", body, 0)
                ref_off = 2 + 4 * ndims
                (elem_ref,) = struct.unpack_from(">H", body, ref_off)
                return LVType(
                    kind=LVTypeKind.ARRAY,
                    underlying_type="Array",
                    element_type=self._ref(elem_ref),
                    dimensions=ndims or 1,
                ), _name_at(body, ref_off + 2)

            if type_code == _REFNUM:
                return self._decode_refnum(body)

            if type_code == _TYPEDEF:
                return self._decode_typedef(body)
        except (struct.error, IndexError):
            return None, None
        # Any unmodelled type code: no type, no name.
        return None, None

    def _decode_cluster(self, body: bytes) -> tuple[LVType, str | None]:
        (fcount,) = struct.unpack_from(">H", body, 0)
        refs = [struct.unpack_from(">H", body, 2 + k * 2)[0] for k in range(fcount)]
        # Out-of-range refs are dropped FIRST so the positional ``field_{i}``
        # fallback numbers the KEPT fields contiguously (no gaps).
        valid = [r for r in refs if r < len(self.lvtypes)]
        fields = [
            ClusterField(name=self.names[r] or f"field_{i}", type=self.lvtypes[r])
            for i, r in enumerate(valid)
        ]
        return LVType(
            kind=LVTypeKind.CLUSTER,
            underlying_type="Cluster",
            fields=fields or None,
        ), _name_at(body, 2 + fcount * 2)

    def _decode_refnum(self, body: bytes) -> tuple[LVType, str | None]:
        """``u16`` sub-kind, then a kind-specific part. A class refnum carries
        ``[class name][label]``: the ``.lvclass`` name is the TYPE, the pstr right
        after it is the terminal LABEL (LabVIEW's default label for a
        dynamic-dispatch terminal IS its class name; a second same-class terminal
        is ``<class> 2``). A parametrized refnum carries its element TD index."""
        refkind = struct.unpack_from(">H", body, 0)[0] if len(body) >= 2 else None
        strs = _all_pstrs(body)
        classname = next((s for s in strs if s.endswith(".lvclass")), None)
        if classname is not None:
            ci = strs.index(classname)
            name = strs[ci + 1] if ci + 1 < len(strs) else None
        else:
            name = strs[-1] if strs else None

        element_type: LVType | None = None
        user_event_name: str | None = None
        if refkind in _PARAMETRIZED_REFNUMS:
            (nclients,) = struct.unpack_from(">H", body, 2)
            if nclients == 1:
                (elem_ref,) = struct.unpack_from(">H", body, 4)
                element_type = self._ref(elem_ref)
                if refkind == 25 and elem_ref < len(self.names):
                    user_event_name = self.names[elem_ref]
            name = _name_at(body, 4 + 2 * nclients)

        return LVType(
            kind=LVTypeKind.PRIMITIVE,
            underlying_type="Refnum",
            ref_type=_REFNUM_KIND.get(refkind) if refkind is not None else None,
            classname=classname,
            element_type=element_type,
            user_event_name=user_event_name,
        ), name

    def _decode_typedef(self, body: bytes) -> tuple[LVType | None, str | None]:
        """``u32`` flag, ``u32`` count + that many qualified-name pstrs (owning
        libraries, then the ``.ctl``), then the inner TypeDesc as a nested
        ``[u16 len][flags][type][body]`` record running to the end of the body.
        The typedef takes the inner type's structure, labelled by the ``.ctl``."""
        (nnames,) = struct.unpack_from(">I", body, 4)
        off = 8
        names: list[str] = []
        for _ in range(nnames):
            s, off = _pstr(body, off)
            names.append(s)
        typedef_name = (
            build_qualified_name(
                [n for n in names[:-1] if n.endswith((".llb", ".lvlib", ".lvclass"))],
                names[-1],
            )
            if names
            else None
        )
        if off + 4 > len(body):
            return None, None
        inner_code = body[off + 3]
        lv, label = self._decode(inner_code, body[off + 4 :])
        if lv is not None:
            lv = replace(lv, typedef_name=typedef_name)
        if _is_type_identity(label) or label == typedef_name:
            label = None
        return lv, label


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
