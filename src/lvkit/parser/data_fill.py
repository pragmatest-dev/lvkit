"""The default data space of a VI: the values it stores outside the diagram.

A VI saved by LabVIEW 9.0 keeps the value of a block-diagram constant, and the
default of a front-panel control, in its default data (``DFDS``) as a
``DataFill`` per data-space type. The object names its type with ``tdOffset``;
the VI's ``TM80`` section adds ``IndexShift`` to turn that into the ``DataFill``
``TypeID``. A type the section marks ``TMFBit11`` has no ``DataFill``: its value
is the type's default.

This module reads that space once (:func:`read_default_data_space`) and turns a
``DataFill`` into the bytes the diagram's ``ConstValue`` / the control's
``DefaultData`` would carry (:func:`flatten_data_fill`), so the decoders that
read those fields read these values unchanged.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import NamedTuple

from ..lv_type_codes import (
    FLOAT_BYTE_WIDTHS,
    INTEGER_BYTE_WIDTHS,
)
from ..models import LVType, LVTypeKind
from ..text_encoding import xml_text_encoding
from .refnum_value import holds_handle

# The TM80 flag that marks a type whose value is its default (no DataFill).
_DEFAULT_VALUE_FLAG = "TMFBit11"

# The first LabVIEW version whose VIs keep their types in one shared pool (VCTP).
# An older VI keeps them inline, and a front-panel control's ``tdOffset`` there
# names its range or options cluster, not its value.
_POOLED_TYPES_SINCE = (8, 6)

_SCALAR_FORMATS: dict[str, str] = {
    "I8": ">b",
    "I16": ">h",
    "I32": ">i",
    "I64": ">q",
    "U8": ">B",
    "U16": ">H",
    "U32": ">I",
    "U64": ">Q",
    "SGL": ">f",
    "DBL": ">d",
}

# The path types (absolute, relative, empty start path) read as bytes.
_PATH_TYPES = ("0", "1", "2")

_VARIANT_TYPES = ("LVVariant", "Variant")
_EMPTY_VARIANT = bytes.fromhex("0900800000000001000400000001000000000000")

# A byte written as a character reference inside a DataFill string.
_BYTE_REFERENCE = re.compile(r"&#(?:x([0-9a-fA-F]{1,2})|([0-9]{1,3}));")


class FlatFill(NamedTuple):
    """A ``DataFill`` as bytes, or the tag that kept it from being read."""

    data: bytes | None
    unsupported_tag: str | None


class ObjectValue(NamedTuple):
    """The stored value of one constant or control: its bytes (hex text) or, when
    it has none, why."""

    value_hex: str | None
    gap: str | None


class _UnsupportedTag(Exception):
    def __init__(self, tag: str) -> None:
        super().__init__(tag)
        self.tag = tag


@dataclass
class DefaultDataSpace:
    """What a VI's default data holds, by data-space ``TypeID``.

    ``index_shift`` is None for a VI whose ``TM80`` section is not decoded (or
    absent); no object of such a VI can be looked up. ``controls_hold_value`` is
    False for a VI saved before LabVIEW 8.6: its constants keep their values
    here, but the entry at a front-panel control's ``tdOffset`` is not the
    control's value.
    """

    index_shift: int | None = None
    controls_hold_value: bool = True
    fills: dict[int, ET.Element] = field(default_factory=dict)
    default_value_types: frozenset[int] = frozenset()
    encoding: str = "cp1252"

    def type_id_of(self, td_offset: str | None) -> int | None:
        """The data-space ``TypeID`` of the object whose ``tdOffset`` text
        (hex) is ``td_offset``; None when the object has none or the VI's
        ``IndexShift`` is not known."""
        if td_offset is None or self.index_shift is None:
            return None
        try:
            return int(td_offset, 16) + self.index_shift
        except ValueError:
            return None

    def fill_value(self, td_offset: str | None) -> ObjectValue:
        """The value of the ``DataFill`` the object's ``tdOffset`` names: its
        bytes, or None with the reason when there is no ``DataFill`` or it holds
        a tag that is not read. No value and no reason when the object names no
        type."""
        type_id = self.type_id_of(td_offset)
        if type_id is None:
            return ObjectValue(None, None)
        fill = self.fills.get(type_id)
        if fill is None:
            return ObjectValue(
                None, f"no value in its default data for data-space type {type_id}"
            )
        flat = flatten_data_fill(fill, self.encoding)
        if flat.data is None:
            return ObjectValue(None, f"the default-data tag {flat.unsupported_tag}")
        return ObjectValue(flat.data.hex(), None)

    def constant_value(
        self,
        td_offset: str | None,
        lv_type: LVType | None,
    ) -> ObjectValue:
        """The value of a block-diagram constant: its ``DataFill``, or, for a
        type the VI marks as holding its default, the default of ``lv_type``."""
        type_id = self.type_id_of(td_offset)
        if type_id is None:
            return ObjectValue(None, None)
        if type_id in self.fills or type_id not in self.default_value_types:
            return self.fill_value(td_offset)
        default = type_default_bytes(lv_type)
        if default is None:
            return ObjectValue(None, f"the default of a {_type_label(lv_type)}")
        return ObjectValue(default.hex(), None)


def iter_data_fills(root: ET.Element) -> list[tuple[int, ET.Element]]:
    """Every ``DataFill`` of the dataspace XML as ``(TypeID, element)``, in
    document order."""
    fills: list[tuple[int, ET.Element]] = []
    for fill in root.iter("DataFill"):
        raw = fill.get("TypeID")
        if raw is None:
            continue
        try:
            fills.append((int(raw), fill))
        except ValueError:
            continue
    return fills


def read_index_shift(root: ET.Element) -> int | None:
    """The ``TM80`` ``IndexShift`` of the dataspace XML (what to add to an
    object's ``tdOffset`` to get its ``DataFill`` ``TypeID``), or None."""
    section = root.find("TM80/Section")
    if section is None:
        return None
    raw = section.get("IndexShift")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _types_pooled(root: ET.Element) -> bool:
    """Whether the VI was saved by LabVIEW 8.6 or later (its ``vers`` version);
    True when the VI records none."""
    version = root.find(".//vers/Section/Version")
    if version is None:
        return True
    try:
        saved = (int(version.get("Major", "")), int(version.get("Minor", "")))
    except ValueError:
        return True
    return saved >= _POOLED_TYPES_SINCE


def read_default_data_space(root: ET.Element) -> DefaultDataSpace:
    """The default data space of the dataspace XML ``root``."""
    shift = read_index_shift(root)
    default_types: set[int] = set()
    if shift is not None:
        section = root.find("TM80/Section")
        clients = section.findall("Client") if section is not None else []
        for position, client in enumerate(clients):
            if client.get(_DEFAULT_VALUE_FLAG) == "1":
                default_types.add(position + shift)
    return DefaultDataSpace(
        index_shift=shift,
        controls_hold_value=_types_pooled(root),
        fills={type_id: fill for type_id, fill in iter_data_fills(root)},
        default_value_types=frozenset(default_types),
        encoding=xml_text_encoding(root),
    )


def flatten_data_fill(fill: ET.Element, encoding: str = "cp1252") -> FlatFill:
    """The bytes a ``DataFill`` flattens to: big-endian numbers, a Boolean as one
    byte, a String as a 4-byte length then its bytes, an Array as a 4-byte size
    per dimension then its elements, a Cluster as its fields in order.

    A tag that is not one of those gives ``FlatFill(None, tag)``."""
    children = list(fill)
    if len(children) != 1:
        return FlatFill(None, "DataFill" if not children else "DataFill (several)")
    try:
        return FlatFill(_flatten(children[0], encoding), None)
    except _UnsupportedTag as unsupported:
        return FlatFill(None, unsupported.tag)


def _flatten(el: ET.Element, encoding: str) -> bytes:
    tag = el.tag
    text = (el.text or "").strip()
    scalar = _SCALAR_FORMATS.get(tag)
    if scalar is not None:
        try:
            if tag in ("SGL", "DBL"):
                return struct.pack(scalar, float(text or "0"))
            return struct.pack(scalar, int(text or "0"))
        except (ValueError, struct.error):
            raise _UnsupportedTag(tag) from None
    if tag == "Boolean":
        return b"\x01" if text not in ("", "0") else b"\x00"
    if tag == "String":
        raw = _string_bytes(el.text or "", encoding)
        return struct.pack(">I", len(raw)) + raw
    if tag == "EB":
        return _pack_enum(el, ">B")
    if tag == "EW":
        return _pack_enum(el, ">H")
    if tag == "EL":
        return _pack_enum(el, ">I")
    if tag == "Path":
        return _path_bytes(el, encoding)
    if tag in ("Cluster", "SpecialDSTMCluster", "TypeDef"):
        return b"".join(_flatten(child, encoding) for child in el)
    if tag == "Array":
        dims = el.findall("dim")
        if len(dims) != 1:
            raise _UnsupportedTag(f"Array of {len(dims)} dimensions")
        out = b""
        for dim in dims:
            out += struct.pack(">I", int((dim.text or "0").strip() or "0"))
        for child in el:
            if child.tag != "dim":
                out += _flatten(child, encoding)
        return out
    raise _UnsupportedTag(tag)


def _pack_enum(el: ET.Element, fmt: str) -> bytes:
    try:
        return struct.pack(fmt, int((el.text or "0").strip() or "0"))
    except (ValueError, struct.error):
        raise _UnsupportedTag(el.tag) from None


def _path_bytes(el: ET.Element, encoding: str) -> bytes:
    """A saved path: ``PTH0``, the length of the rest, the path type and the
    component count (2 bytes each), then each component as a length byte and its
    bytes."""
    if el.get("Ident") != "PTH0" or el.get("TpVal") not in _PATH_TYPES:
        raise _UnsupportedTag("Path")
    try:
        path_type = int(el.get("TpVal", ""))
        body = struct.pack(">HH", path_type, len(el.findall("String")))
        for component in el.findall("String"):
            raw = _string_bytes(component.text or "", encoding)
            body += struct.pack(">B", len(raw)) + raw
    except (ValueError, struct.error):
        raise _UnsupportedTag("Path") from None
    return b"PTH0" + struct.pack(">I", len(body)) + body


def _string_bytes(text: str, encoding: str) -> bytes:
    """A DataFill string as bytes: a character reference is the byte it names,
    any other character is encoded with the VI's text encoding."""
    out = bytearray()
    position = 0
    try:
        for match in _BYTE_REFERENCE.finditer(text):
            out += text[position : match.start()].encode(encoding)
            hex_digits, decimal = match.groups()
            out.append(int(hex_digits, 16) if hex_digits else int(decimal))
            position = match.end()
        out += text[position:].encode(encoding)
    except (UnicodeEncodeError, ValueError):
        raise _UnsupportedTag("String") from None
    return bytes(out)


def type_default_bytes(lv_type: LVType | None) -> bytes | None:
    """The bytes of the default value of ``lv_type``: zero for a number, one zero
    byte for a Boolean, an empty String, an Array with every dimension 0, a
    Cluster of such. None for any other type."""
    if lv_type is None:
        return None
    if lv_type.kind == LVTypeKind.ARRAY:
        return b"\x00\x00\x00\x00" * (lv_type.dimensions or 1)
    if lv_type.kind == LVTypeKind.CLUSTER:
        if lv_type.fields is None:
            return None
        parts = [type_default_bytes(f.type) for f in lv_type.fields]
        if any(part is None for part in parts):
            return None
        return b"".join(part for part in parts if part is not None)
    if lv_type.kind != LVTypeKind.PRIMITIVE:
        return None
    name = lv_type.underlying_type
    if name == "Boolean":
        return b"\x00"
    if name == "String":
        return b"\x00\x00\x00\x00"
    if holds_handle(lv_type):
        # A session reference is never saved, so a refnum constant is Not a
        # Refnum: handle 0. Of the refnum constants (no class, no tag) the
        # corpus pairs with a TMFBit11 type, 24 of 24 hold handle 0.
        return b"\x00\x00\x00\x00"
    if name == "Path":
        # An empty path: PTH0, a 4-byte body of path type 0 and 0 components.
        # Of the Path constants with a TMFBit11 type, 34 of 34 hold exactly these
        # 12 bytes.
        return b"PTH0\x00\x00\x00\x04\x00\x00\x00\x00"
    if name in _VARIANT_TYPES:
        # An empty Variant: no value and no attributes. Of the Variant constants
        # with a TMFBit11 type, 45 of 45 hold exactly these 20 bytes.
        return _EMPTY_VARIANT
    width = INTEGER_BYTE_WIDTHS.get(name or "") or FLOAT_BYTE_WIDTHS.get(name or "")
    return b"\x00" * width if width else None


def _type_label(lv_type: LVType | None) -> str:
    if lv_type is None:
        return "type that is not known"
    if lv_type.underlying_type == "Refnum" and lv_type.classname:
        return f"class value of {lv_type.classname}"
    if lv_type.underlying_type == "Refnum" and lv_type.ref_type:
        return f"{lv_type.ref_type} refnum"
    return lv_type.underlying_type or str(lv_type.kind.value)
