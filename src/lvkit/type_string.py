"""The ``type string`` of Variant To Flattened String and Flattened String To
Variant: a LabVIEW type descriptor as an array of 16-bit integers.

NI's "Type Descriptors" page (docs-be bundle ``labview``): "When you use the Variant
To Flattened String function, LabVIEW flattens only the variant and discards any
attributes. Using this function, a type descriptor has the following format:
[size][type code][type specific info][name (if applicable)]"; "[size] is the first
word, in bytes, and includes the size word"; "[type code] is the second word.
LabVIEW reserves the high-order byte of the type code for internal use"; "You can
append a name (a Pascal string) to any type descriptor, which increases the value
of [size] by the length of the name rounded up to a multiple of two."

The page's descriptors, as 16-bit words in hexadecimal (all read here):

- a number: ``0004 xx <code>`` (``0004 0003`` is a 32-bit integer);
- a Boolean: ``0004 0021``; a string: ``0008 0030 FFFF FFFF`` ("the only value
  currently encoded is hexadecimal FFFFFFFF, which indicates variable sized");
- an array: ``<nn> 0040 <k dims> <dimension sizes> <element type descriptor>``, a
  1-D array of doubles is ``000E 0040 0001 FFFF FFFF 0004 000A`` and a 2-D array of
  Booleans ``0012 0040 0002 FFFF FFFF FFFF FFFF 0004 0021``;
- a cluster: ``<nn> 0050 <k elems> <element type descriptors>``, a cluster of an
  I16 and a U32 is ``000E 0050 0002 0004 0002 0004 0007``;
- an enum: ``<nn> xx 15|16|17 <k> <k pstrs>``, three items ``am``, ``fm``,
  ``fm stereo`` in an 8-bit enum are ``0016 0015 0003 0261 6D02 666D 0966 6D20
  7374 6572 656F``.

An aggregate type holds its members' descriptors inline, so a type string is one
descriptor. The words are signed 16-bit numbers when held in the array of I16 the
terminal carries (``FFFF`` is -1). The same descriptors, one per type and
referring to each other by index, are what a VI file stores
(``parser/td_pool.py``); ``decode`` lays an inline descriptor out as that list and
reads it with that decoder.

``encode`` writes the types ``codegen/flat.py`` has a layout for among those the
page documents: numbers, Booleans, strings, arrays, clusters and enums. It writes
no names on cluster members (flattening does not read them), and pads an enum's
item list to an even number of bytes, as the VI file does. Any other type is a
``TypeStringError``.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

from lvkit.lv_type_codes import (
    TD_FULL_TYPE,
    is_boolean_type_name,
    is_numeric_type_name,
    is_string_type_name,
)
from lvkit.models import LVType, LVTypeKind
from lvkit.parser.td_pool import RawTD, TdPool

_VARIABLE_SIZE = 0xFFFF
_MAX_SIZE = 0x7FFF

# The numbers the page documents, by type code (physical quantities carry base
# units and are not written).
_NUMBER_CODES = {
    TD_FULL_TYPE[name].name: TD_FULL_TYPE[name]
    for name in (
        "NumInt8",
        "NumInt16",
        "NumInt32",
        "NumInt64",
        "NumUInt8",
        "NumUInt16",
        "NumUInt32",
        "NumUInt64",
        "NumFloat32",
        "NumFloat64",
        "NumFloatExt",
        "NumComplex64",
        "NumComplex128",
        "NumComplexExt",
    )
}
_ENUM_CODES = {
    name: TD_FULL_TYPE[name] for name in ("UnitUInt8", "UnitUInt16", "UnitUInt32")
}
# What a decoded descriptor may be; anything else has no flattened layout here.
_DECODED_KINDS = frozenset(
    {LVTypeKind.PRIMITIVE, LVTypeKind.ENUM, LVTypeKind.ARRAY, LVTypeKind.CLUSTER}
)


class TypeStringError(Exception):
    """A type that has no type string, or a type string that is not one."""


def encode(lv_type: LVType) -> list[int]:
    """The type string of ``lv_type``: its descriptor as signed 16-bit numbers."""
    return [_signed(word) for word in _descriptor(lv_type)]


def decode(numbers: Sequence[int]) -> LVType:
    """The type a type string describes; raises ``TypeStringError`` for one that
    is not a single readable descriptor of a type ``encode`` writes."""
    data = b"".join(struct.pack(">H", number & 0xFFFF) for number in numbers)
    raws: list[RawTD] = []
    root, end = _read(data, 0, raws)
    if end != len(data):
        raise TypeStringError(
            f"the type string has {len(data) - end} bytes after its descriptor"
        )
    pool = TdPool(raws)
    if pool.failures:
        raise TypeStringError(next(iter(pool.failures.values())))
    result = pool.lvtypes[root]
    _require_known(result)
    return result


def _signed(word: int) -> int:
    return word - 0x10000 if word & 0x8000 else word


def _descriptor(lv_type: LVType) -> list[int]:
    """The 16-bit words of ``lv_type``'s descriptor."""
    underlying = lv_type.underlying_type or ""
    if lv_type.kind == LVTypeKind.ARRAY:
        element = lv_type.element_type
        if element is None or element.kind == LVTypeKind.ARRAY:
            raise TypeStringError("an array of arrays or of an unknown type")
        dimensions = lv_type.dimensions or 1
        inner = _descriptor(element)
        head = [0x0040, dimensions, *([_VARIABLE_SIZE] * (2 * dimensions))]
        return _sized(head + inner)
    if lv_type.kind == LVTypeKind.CLUSTER:
        if not lv_type.fields:
            raise TypeStringError("a cluster with no members")
        members: list[int] = []
        for member in lv_type.fields:
            if member.type is None:
                raise TypeStringError("a cluster member of an unknown type")
            members += _descriptor(member.type)
        return _sized([0x0050, len(lv_type.fields), *members])
    if lv_type.kind == LVTypeKind.ENUM:
        return _enum(lv_type)
    if lv_type.kind != LVTypeKind.PRIMITIVE:
        raise TypeStringError(f"a value of type {lv_type.kind.value!r}")
    if is_boolean_type_name(underlying):
        return _sized([TD_FULL_TYPE.Boolean.value])
    if is_string_type_name(underlying):
        return _sized([TD_FULL_TYPE.String.value, _VARIABLE_SIZE, _VARIABLE_SIZE])
    if underlying in _NUMBER_CODES:
        return _sized([_NUMBER_CODES[underlying].value])
    if is_numeric_type_name(underlying):
        raise TypeStringError(f"a number of type {underlying!r}")
    raise TypeStringError(f"a value of type {underlying or lv_type.kind.value!r}")


def _enum(lv_type: LVType) -> list[int]:
    code = _ENUM_CODES.get(lv_type.underlying_type or "")
    if code is None or lv_type.values is None:
        raise TypeStringError("an enum with no recorded items")
    labels = sorted(lv_type.values.items(), key=lambda item: item[1].value)
    items = b"".join(
        bytes([len(raw)]) + raw
        for raw in (label.encode("latin-1") for label, _ in labels)
    )
    if len(items) % 2:
        items += b"\x00"
    words = struct.unpack(f">{len(items) // 2}H", items)
    return _sized([code.value, len(labels), *words])


def _sized(words: list[int]) -> list[int]:
    """``words`` (the type code onward) behind the size word, which counts bytes
    and itself."""
    size = 2 * (len(words) + 1)
    if size > _MAX_SIZE:
        raise TypeStringError("a type whose descriptor is too large")
    return [size, *words]


def _var_size(value: int) -> bytes:
    """A count or index as the VI file stores it: 16 bits, or 32 with the high bit
    set for a value of 32768 or more."""
    if value < 0x8000:
        return struct.pack(">H", value)
    return struct.pack(">I", value | 0x80000000)


def _word(data: bytes, pos: int) -> int:
    if pos + 2 > len(data):
        raise TypeStringError("the type string ends inside a descriptor")
    return int.from_bytes(data[pos : pos + 2], "big")


def _read(data: bytes, pos: int, raws: list[RawTD]) -> tuple[int, int]:
    """Read the inline descriptor at ``pos``, appending it to ``raws`` after the
    descriptors of its members, each referred to by index. Returns the
    descriptor's index and the position after it."""
    size = _word(data, pos)
    end = pos + size
    if size < 4 or end > len(data):
        raise TypeStringError(f"a descriptor has a size of {size} bytes it cannot hold")
    flags, code = data[pos + 2], data[pos + 3]
    body_start = pos + 4
    if code == TD_FULL_TYPE.Array:
        dimensions = _word(data, body_start)
        member = body_start + 2 + 4 * dimensions
        index, after = _read_member(data, member, end, raws)
        body = data[body_start:member] + _var_size(index) + data[after:end]
    elif code == TD_FULL_TYPE.Cluster:
        count = _word(data, body_start)
        refs = bytearray()
        after = body_start + 2
        for _ in range(count):
            index, after = _read_member(data, after, end, raws)
            refs += _var_size(index)
        body = _var_size(count) + bytes(refs) + data[after:end]
    else:
        body = data[body_start:end]
    raws.append(RawTD(code, flags, body))
    return len(raws) - 1, end


def _read_member(
    data: bytes, pos: int, parent_end: int, raws: list[RawTD]
) -> tuple[int, int]:
    index, after = _read(data, pos, raws)
    if after > parent_end:
        raise TypeStringError("a member descriptor runs past its parent's size")
    return index, after


def _require_known(lv_type: LVType) -> None:
    if lv_type.kind not in _DECODED_KINDS:
        raise TypeStringError(f"a value of type {lv_type.kind.value!r}")
    if lv_type.element_type is not None:
        _require_known(lv_type.element_type)
    for member in lv_type.fields or ():
        if member.type is not None:
            _require_known(member.type)
    underlying = lv_type.underlying_type or ""
    if lv_type.kind == LVTypeKind.PRIMITIVE and not (
        underlying in _NUMBER_CODES
        or is_boolean_type_name(underlying)
        or underlying == TD_FULL_TYPE.String.name
    ):
        raise TypeStringError(f"a value of type {underlying!r}")
