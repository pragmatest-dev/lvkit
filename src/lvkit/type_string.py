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

A time stamp is the page's waveform-family type code ``0054`` followed by its
subtype word, ``0006``: "The time stamp has a subtype code of 6 ... LabVIEW stores
a time stamp as a cluster of four integers where the first two signed integers
(64 bits) ... The next two unsigned integers (64 bits)". The page's example size
``001C`` is 28 bytes: the 6 bytes of size, code and subtype and a 22-byte cluster
of four 32-bit integers (``0016 0050 0004`` and four 4-byte numbers), so the
descriptor is ``001C 0054 0006 0016 0050 0004 0004 0003 0004 0003 0004 0007 0004
0007``, the cluster read as I32, I32, U32, U32 and a name appended after it. The
page's other waveform-family descriptors are not written.

``encode`` writes the types ``codegen/flat.py`` has a layout for among those the
page documents: numbers, Booleans, strings, arrays, clusters, enums and time
stamps. A cluster
member with a label is written named, as the page's I16 named ``radio`` is
(``000A 4002 0572 6164 696F``: the 0x40 flag on the type code, the name a Pascal
string padded to an even length, counted in the size word); an unlabelled member
is written without a name. An enum's item list is padded to an even number of
bytes, as the VI file does. Any other type is a
``TypeStringError``.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

from lvkit.lv_type_codes import (
    MEASURE_DATA_FLAVOR,
    TD_FULL_TYPE,
    is_boolean_type_name,
    is_numeric_type_name,
    is_string_type_name,
)
from lvkit.models import LVType, LVTypeKind
from lvkit.parser.td_pool import RawTD, TdPool

VARIABLE_SIZE = 0xFFFF
_MAX_SIZE = 0x7FFF
# The flag in the type code's high byte that a name follows the descriptor.
NAME_FLAG = 0x4000

# The numbers the page documents, by type code (physical quantities carry base
# units and are not written).
NUMBER_CODES = {
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
ENUM_CODES = {
    name: TD_FULL_TYPE[name] for name in ("UnitUInt8", "UnitUInt16", "UnitUInt32")
}
# What a decoded descriptor may be; anything else has no flattened layout here.
_DECODED_KINDS = frozenset(
    {LVTypeKind.PRIMITIVE, LVTypeKind.ENUM, LVTypeKind.ARRAY, LVTypeKind.CLUSTER}
)


class TypeStringError(Exception):
    """A type that has no type string, or a type string that is not one."""


def encode(lv_type: LVType, name: str | None = None) -> list[int]:
    """The type string of ``lv_type``: its descriptor as signed 16-bit numbers,
    with ``name`` (the data's own label, when known) appended as the page
    describes. A cluster member is written with its label when it has one."""
    return [_signed(word) for word in _descriptor(lv_type, name)]


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


def _descriptor(lv_type: LVType, name: str | None = None) -> list[int]:
    """The 16-bit words of ``lv_type``'s descriptor, ending in ``name`` when
    given."""
    underlying = lv_type.underlying_type or ""
    if lv_type.kind == LVTypeKind.ARRAY:
        element = lv_type.element_type
        if element is None or element.kind == LVTypeKind.ARRAY:
            raise TypeStringError("an array of arrays or of an unknown type")
        dimensions = lv_type.dimensions or 1
        inner = _descriptor(element)
        head = [0x0040, dimensions, *([VARIABLE_SIZE] * (2 * dimensions))]
        return _sized(head + inner, name)
    if lv_type.kind == LVTypeKind.CLUSTER:
        if not lv_type.fields:
            raise TypeStringError("a cluster with no members")
        members: list[int] = []
        for member in lv_type.fields:
            if member.type is None:
                raise TypeStringError("a cluster member of an unknown type")
            members += _descriptor(
                member.type, member.name if member.labelled and member.name else None
            )
        return _sized([0x0050, len(lv_type.fields), *members], name)
    if lv_type.kind == LVTypeKind.ENUM:
        return _enum(lv_type, name)
    if lv_type.kind != LVTypeKind.PRIMITIVE:
        raise TypeStringError(f"a value of type {lv_type.kind.value!r}")
    if lv_type.is_timestamp:
        return _timestamp(name)
    if is_boolean_type_name(underlying):
        return _sized([TD_FULL_TYPE.Boolean.value], name)
    if is_string_type_name(underlying):
        return _sized([TD_FULL_TYPE.String.value, VARIABLE_SIZE, VARIABLE_SIZE], name)
    if underlying in NUMBER_CODES:
        return _sized([NUMBER_CODES[underlying].value], name)
    if is_numeric_type_name(underlying):
        raise TypeStringError(f"a number of type {underlying!r}")
    raise TypeStringError(f"a value of type {underlying or lv_type.kind.value!r}")


# The four integers a time stamp's cluster holds, in order.
TIMESTAMP_MEMBERS = ("NumInt32", "NumInt32", "NumUInt32", "NumUInt32")


def _timestamp(name: str | None) -> list[int]:
    """The descriptor of a time stamp: the waveform-family code, the time stamp's
    subtype word and the descriptor of its cluster of four integers."""
    members = [
        word
        for member in TIMESTAMP_MEMBERS
        for word in _sized([NUMBER_CODES[member].value])
    ]
    cluster = _sized([TD_FULL_TYPE.Cluster.value, len(TIMESTAMP_MEMBERS), *members])
    return _sized(
        [TD_FULL_TYPE.MeasureData.value, MEASURE_DATA_FLAVOR.TimeStamp.value, *cluster],
        name,
    )


def _enum(lv_type: LVType, name: str | None) -> list[int]:
    code = ENUM_CODES.get(lv_type.underlying_type or "")
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
    return _sized([code.value, len(labels), *words], name)


def _sized(words: list[int], name: str | None = None) -> list[int]:
    """``words`` (the type code onward) behind the size word, which counts bytes
    and itself. A ``name`` sets the "has a name" flag (0x40) in the high byte of
    the type code and follows the type-specific words as a Pascal string, padded
    to an even number of bytes."""
    if name is not None:
        raw = name.encode("latin-1")
        if len(raw) > 0xFF:
            raise TypeStringError("a name longer than 255 bytes")
        text = bytes([len(raw)]) + raw
        if len(text) % 2:
            text += b"\x00"
        words = [
            words[0] | NAME_FLAG,
            *words[1:],
            *struct.unpack(f">{len(text) // 2}H", text),
        ]
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
    elif code == TD_FULL_TYPE.MeasureData:
        body = _measure_data_body(data, body_start, end)
    else:
        body = data[body_start:end]
    raws.append(RawTD(code, flags, body))
    return len(raws) - 1, end


def _measure_data_body(data: bytes, start: int, end: int) -> bytes:
    """The body a VI file stores for the waveform-family descriptor whose inline
    form is ``data[start:end]``: the subtype word and the name. A time stamp's
    inline cluster of four integers is checked against the one ``encode`` writes
    and dropped."""
    flavor = _word(data, start)
    if flavor != MEASURE_DATA_FLAVOR.TimeStamp:
        return data[start:end]
    cluster = b"".join(
        struct.pack(">H", word & 0xFFFF) for word in _timestamp(None)[3:]
    )
    inner = start + 2
    if data[inner : inner + len(cluster)] != cluster:
        raise TypeStringError("a time stamp that does not hold four 32-bit integers")
    return data[start:inner] + data[inner + len(cluster) : end]


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
        lv_type.is_timestamp
        or underlying in NUMBER_CODES
        or is_boolean_type_name(underlying)
        or underlying == TD_FULL_TYPE.String.name
    ):
        raise TypeStringError(f"a value of type {underlying!r}")
