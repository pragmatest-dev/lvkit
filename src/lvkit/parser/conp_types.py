"""Decode the pre-LV9 ``CONP`` block — the connector-pane type pool — into named
``LVType``s, one per connector-pane terminal.

Pre-LV9 (LV8.x) VIs have no ``VCTP`` consolidated type pool; the connector-pane
terminal types live in ``CONP`` instead, in the SAME bottom-up TypeDesc format
VCTP uses — but pylabview leaves ``CONP`` raw for old VIs (its block parser has a
``# we do not know how to parse complex form of CONP`` TODO). The extractor dumps
the decompressed block as a ``*_CONP.bin`` sidecar, which we decode here.

Unlike the FP-heap reconstruction (``fp_heap_type``), CONP carries the **names**:
cluster field names, the terminal name, the class name of a class refnum, enum
item labels, and the refnum kind. It is therefore the authoritative source for a
pre-LV9 VI's *interface* — but ONLY the interface: a front-panel control not
wired to the connector pane is not in CONP (use the heap reconstruction there).

Binary layout::

    u32  count
    count x TypeDesc:
        u16  total_len      (includes these 2 bytes)
        u8   flags
        u8   type_code      (LabVIEW TD_FULL_TYPE)
        ...type-specific body, ending in a Pascal-string label...

TypeDescs are bottom-up: a cluster/array names its members by INDEX into the
TDs decoded before it. One TD has ``type_code == 0xf0`` — the connector-pane map:
``u16 nslots`` then ``nslots x u16`` TD-index per slot (empty slots point at a
``0x00`` Void TD). That array, in slot order, is the terminal list.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from ..models import ClusterField, EnumValue, LVType, enum_values_from_labels

# TD_FULL_TYPE code -> the ``underlying_type`` string ``LVType.lv_label`` expects
# (so a CONP-decoded scalar renders identically to a VCTP-decoded one).
_SCALAR_CODE: dict[int, str] = {
    0x01: "NumInt8", 0x02: "NumInt16", 0x03: "NumInt32", 0x04: "NumInt64",
    0x05: "NumUInt8", 0x06: "NumUInt16", 0x07: "NumUInt32", 0x08: "NumUInt64",
    0x09: "NumFloat32", 0x0A: "NumFloat64", 0x0B: "NumFloatExt",
    0x20: "Boolean", 0x21: "Boolean",
    0x30: "String", 0x31: "String", 0x34: "String", 0x35: "String",
    0x32: "Path",
}
_ENUM_CODES = frozenset({0x15, 0x16, 0x17})  # UnitUInt8 / 16 / 32

# Refnum sub-kind code (u16 at the start of a 0x70 body) -> the RefType name
# LabVIEW uses (mirrors pylabview's REFNUM_TYPE / what the VCTP path stores in
# ``LVType.ref_type``), so a CONP-decoded refnum renders identically — e.g.
# ``Queue refnum``, ``LVObjCtl refnum`` — to a VCTP-decoded one.
_REFNUM_KIND: dict[int, str] = {
    0: "Generic", 1: "DataLog", 2: "ByteStream", 3: "Device", 4: "Occurrence",
    5: "TCPNetConn", 7: "AutoRef", 8: "LVObjCtl", 9: "Menu", 11: "Imaq",
    13: "DataSocket", 14: "VisaRef", 15: "IVIRef", 16: "UDPNetConn",
    17: "NotifierRef", 18: "Queue", 19: "IrdaNetConn", 20: "UsrDefined",
    21: "UsrDefndTag", 23: "EventReg", 24: "DotNet", 25: "UserEvent",
    27: "Callback", 29: "UsrDefTagFlt", 30: "UDClassInst", 31: "BluetoothCon",
    32: "DataValueRef", 33: "FIFORef", 34: "TDMSFile",
}
_STRINGISH = frozenset({0x30, 0x31, 0x34, 0x35})
_CLUSTER, _ARRAY, _REFNUM = 0x50, 0x40, 0x70
_VOID, _TYPEDEF, _CONPANE = 0x00, 0xF1, 0xF0


@dataclass(frozen=True)
class ConpTerminal:
    """One connector-pane terminal recovered from CONP: its slot, its label, and
    its fully-named type."""

    slot: int
    name: str | None
    lv_type: LVType | None


def _pstr(data: bytes, off: int) -> tuple[str, int]:
    """Read a length-prefixed (1-byte) latin-1 string at ``off``; return it and
    the offset past it."""
    n = data[off]
    return data[off + 1:off + 1 + n].decode("latin1"), off + 1 + n


def _is_type_identity(s: str | None) -> bool:
    """A ``.lvclass``/``.ctl`` string is a TYPE identity, never a terminal label
    — guards against a typedef name leaking in as a terminal name when a
    ``0xf1`` typedef body carries no distinct label (see ``_decode_typedef``).
    (The refnum branch keeps its class name as a valid label deliberately — a
    class terminal's default label IS its class name — so it does NOT call this.)
    """
    return bool(s) and (".lvclass" in s or ".ctl" in s)  # type: ignore[arg-type]


def _all_pstrs(body: bytes) -> list[str]:
    """Every length-prefixed string in ``body`` (best-effort; used for the
    name-bearing typedef/refnum bodies whose fixed layout is only partly known).
    """
    out: list[str] = []
    off = 0
    while off < len(body):
        n = body[off]
        if n == 0 or off + 1 + n > len(body):
            off += 1
            continue
        chunk = body[off + 1:off + 1 + n]
        if all(32 <= c < 127 for c in chunk):
            out.append(chunk.decode("latin1"))
            off += 1 + n
        else:
            off += 1
    return out


def _decode_enum(body: bytes, start: int) -> tuple[dict[str, EnumValue], int]:
    """Enum body at ``start``: ``u16 count`` then ``count`` label pstrs. Returns
    the ordinal-valued members and the offset past them."""
    (count,) = struct.unpack_from(">H", body, start)
    off = start + 2
    labels: list[str] = []
    for _ in range(count):
        label, off = _pstr(body, off)
        labels.append(label)
    return enum_values_from_labels(labels), off


class _ConpDecoder:
    def __init__(self, data: bytes):
        self.data = data
        self.tds: list[tuple[int, bytes]] = []   # (type_code, body)
        self.lvtypes: list[LVType | None] = []
        self.names: list[str | None] = []
        self.slot_map: list[int] = []

    def decode(self) -> list[ConpTerminal]:
        data = self.data
        if len(data) < 6:
            return []
        (count,) = struct.unpack_from(">I", data, 0)
        off = 4
        for _ in range(count):
            if off + 4 > len(data):
                break
            (tlen,) = struct.unpack_from(">H", data, off)
            if tlen < 4 or off + tlen > len(data):
                break
            type_code = data[off + 3]
            body = data[off + 4:off + tlen]
            self.tds.append((type_code, body))
            off += tlen

        # First pass: decode every TD to (LVType, name), resolving member refs
        # against TDs already decoded (the list is strictly bottom-up).
        for type_code, body in self.tds:
            if type_code == _CONPANE:
                self.slot_map = self._decode_conpane(body)
                self.lvtypes.append(None)
                self.names.append(None)
            else:
                lv, name = self._decode_td(type_code, body)
                self.lvtypes.append(lv)
                self.names.append(name)

        return self._terminals()

    def _decode_conpane(self, body: bytes) -> list[int]:
        if len(body) < 2:
            return []
        (nslots,) = struct.unpack_from(">H", body, 0)
        slots: list[int] = []
        for s in range(nslots):
            o = 2 + s * 2
            if o + 2 > len(body):
                break
            slots.append(struct.unpack_from(">H", body, o)[0])
        return slots

    def _decode_td(
        self, type_code: int, body: bytes,
    ) -> tuple[LVType | None, str | None]:
        try:
            if type_code in _SCALAR_CODE:
                name_off = 4 if type_code in _STRINGISH else 0
                name = self._name_at(body, name_off)
                return LVType(
                    kind="primitive", underlying_type=_SCALAR_CODE[type_code],
                ), name

            if type_code in _ENUM_CODES:
                values, off = _decode_enum(body, 0)
                name = self._name_at(body, off)
                return LVType(kind="enum", underlying_type="Enum",
                              values=values), name

            if type_code == _CLUSTER:
                (fcount,) = struct.unpack_from(">H", body, 0)
                refs = [
                    struct.unpack_from(">H", body, 2 + k * 2)[0]
                    for k in range(fcount)
                ]
                name = self._name_at(body, 2 + fcount * 2)
                # Filter out-of-range refs FIRST so the positional ``field_{i}``
                # fallback numbers the KEPT fields contiguously (no gaps).
                valid = [r for r in refs if r < len(self.lvtypes)]
                fields = [
                    ClusterField(
                        name=self.names[r] or f"field_{i}",
                        type=self.lvtypes[r],
                    )
                    for i, r in enumerate(valid)
                ]
                return LVType(kind="cluster", underlying_type="Cluster",
                              fields=fields or None), name

            if type_code == _ARRAY:
                # u16 ndims, then u16 element-TD ref; label trails.
                (ndims,) = struct.unpack_from(">H", body, 0)
                (elem_ref,) = struct.unpack_from(">H", body, 2)
                elem = (
                    self.lvtypes[elem_ref]
                    if elem_ref < len(self.lvtypes) else None
                )
                name = self._trailing_name(body)
                return LVType(kind="array", underlying_type="Array",
                              element_type=elem, dimensions=ndims or 1), name

            if type_code == _REFNUM:
                # u16 sub-kind, then for a class refnum: [class name][label].
                # The class name (``.lvclass``) is the TYPE; the pstr right after
                # it is the terminal LABEL. LabVIEW's default label for a
                # dynamic-dispatch terminal IS its class name, and a SECOND
                # same-class terminal is disambiguated ``<class> 2`` — both are
                # real labels (a developer rename, e.g. ``reference in``, sits in
                # the same slot).
                refkind = (
                    struct.unpack_from(">H", body, 0)[0] if len(body) >= 2
                    else None
                )
                strs = _all_pstrs(body)
                classname = next(
                    (s for s in strs if s.endswith(".lvclass")), None
                )
                if classname is not None:
                    ci = strs.index(classname)
                    name = strs[ci + 1] if ci + 1 < len(strs) else None
                else:
                    name = strs[-1] if strs else None
                return LVType(
                    kind="primitive", underlying_type="Refnum",
                    ref_type=_REFNUM_KIND.get(refkind) if refkind is not None
                    else None,
                    classname=classname,
                ), name

            if type_code == _TYPEDEF:
                return self._decode_typedef(body)

            if type_code == _VOID:
                return None, None
        except (struct.error, IndexError):
            return None, None
        return None, None

    def _decode_typedef(
        self, body: bytes,
    ) -> tuple[LVType | None, str | None]:
        """A ``0xf1`` typedef wraps an inner type and names the ``.ctl``. The
        pstrs are, in order: the typedef ``.ctl`` name, the inner enum's item
        labels (when the inner type is an enum), then the terminal label."""
        strs = _all_pstrs(body)
        typedef_name = next((s for s in strs if s.endswith(".ctl")), None)
        term_name = strs[-1] if strs else None
        if _is_type_identity(term_name):
            term_name = None
        # Inner enum: locate a ``UnitUInt*`` code followed by ``u16 count``.
        for code in _ENUM_CODES:
            idx = body.find(bytes([code]))
            while idx != -1:
                try:
                    values, _ = _decode_enum(body, idx + 1)
                except (struct.error, IndexError):
                    values = {}
                if values and all(
                    v.value == i for i, v in enumerate(values.values())
                ):
                    return LVType(
                        kind="enum", underlying_type="Enum",
                        values=values, typedef_name=typedef_name,
                    ), term_name
                idx = body.find(bytes([code]), idx + 1)
        return None, term_name

    def _name_at(self, body: bytes, off: int) -> str | None:
        if 0 <= off < len(body):
            try:
                s, _ = _pstr(body, off)
                return s or None
            except IndexError:
                return None
        return None

    @staticmethod
    def _trailing_name(body: bytes) -> str | None:
        strs = _all_pstrs(body)
        return strs[-1] if strs else None

    def _terminals(self) -> list[ConpTerminal]:
        out: list[ConpTerminal] = []
        for slot, tdi in enumerate(self.slot_map):
            if tdi >= len(self.tds):
                continue
            if self.tds[tdi][0] == _VOID:
                continue  # empty connector-pane slot
            out.append(ConpTerminal(
                slot=slot, name=self.names[tdi], lv_type=self.lvtypes[tdi],
            ))
        return out


def conp_sidecar_path(xml_path: Path | str) -> Path:
    """The ``*_CONP.bin`` sidecar beside a VI's extracted XML, derived from ANY
    of its sibling XMLs — the main ``<stem>.xml`` or a ``<stem>_FPHb.xml`` /
    ``_BDHb.xml`` heap. One place so the two callers (front_panel type overlay,
    vi.py name recovery) can't drift on the path."""
    p = Path(xml_path)
    stem = p.name
    for suffix in ("_FPHb.xml", "_BDHb.xml", ".xml"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return p.with_name(f"{stem}_CONP.bin")


def decode_conp_terminals(conp_bytes: bytes) -> list[ConpTerminal]:
    """Decode a ``*_CONP.bin`` sidecar into its connector-pane terminals.

    Returns ``[]`` for an empty/stub CONP (an LV9+ VI, whose types live in VCTP)
    or on any structural surprise — the caller then keeps its existing
    resolution. Never raises on malformed input.
    """
    try:
        return _ConpDecoder(conp_bytes).decode()
    except (struct.error, IndexError, ValueError):
        return []
