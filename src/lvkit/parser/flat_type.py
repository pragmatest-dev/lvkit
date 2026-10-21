"""Flattened type descriptors, as a LabVIEW Variant stores them inline.

A flattened Variant carries the descriptors of the types it uses (NI "Type
Descriptors": https://docs-be.ni.com/api/bundle/labview/page/type-descriptors.html):
each one is ``[size][flags][type code][type-specific info][name]``, where ``size``
is a 16-bit count of bytes that includes the size word, or 32 bits with the high
bit set when it does not fit 15 bits. Aggregate types (an array, a cluster) name
their members by index into the same list.

This reads the descriptors a stored value can need to decode a Variant's contents:
numbers, enums, Booleans, strings, paths, pictures, arrays, clusters and nested
Variants. Any other type code is reported as unsupported (``None``), never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from lvkit.models import ClusterField, LVType, LVTypeKind

_NUMERIC = {
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
    # Physical quantities flatten as the float they wrap.
    0x19: "NumFloat32",
    0x1A: "NumFloat64",
    0x1B: "NumFloatExt",
    0x1C: "NumComplex64",
    0x1D: "NumComplex128",
    0x1E: "NumComplexExt",
}
_ENUM = {0x15: "UnitUInt8", 0x16: "UnitUInt16", 0x17: "UnitUInt32"}
_PRIMITIVE = {
    0x00: "Void",
    0x21: "Boolean",
    0x30: "String",
    0x32: "Path",
    0x33: "Picture",
    0x53: "LVVariant",
}
_ARRAY = 0x40
_CLUSTER = 0x50


@dataclass(frozen=True)
class FlatTD:
    """One flattened type descriptor: its type code and its type-specific bytes
    (everything after the 4-byte header, up to the descriptor's size)."""

    type_code: int
    body: bytes


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


def read_descriptors(
    data: bytes | memoryview, pos: int, count: int
) -> tuple[list[FlatTD], int] | None:
    """``count`` consecutive descriptors starting at ``pos``; the list and the
    position after the last one, or None when the data ends first."""
    descriptors: list[FlatTD] = []
    for _ in range(count):
        size_field = read_var_size(data, pos)
        if size_field is None:
            return None
        size, body_start = size_field
        header_len = body_start - pos + 2
        end = pos + size
        if size < header_len or end > len(data):
            return None
        descriptors.append(
            FlatTD(
                type_code=data[body_start + 1], body=bytes(data[body_start + 2 : end])
            )
        )
        pos = end
    return descriptors, pos


def descriptor_type(descriptors: list[FlatTD], index: int) -> LVType | None:
    """The LVType of descriptor ``index``, or None for a type code this does not
    read."""
    return _build(descriptors, index, frozenset())


def _build(
    descriptors: list[FlatTD], index: int, active: frozenset[int]
) -> LVType | None:
    if not 0 <= index < len(descriptors) or index in active:
        return None
    td = descriptors[index]
    code = td.type_code
    active = active | {index}
    if code in _NUMERIC:
        return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=_NUMERIC[code])
    if code in _ENUM:
        return LVType(kind=LVTypeKind.ENUM, underlying_type=_ENUM[code])
    if code in _PRIMITIVE:
        return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=_PRIMITIVE[code])
    if code == _ARRAY:
        return _array(descriptors, td, active)
    if code == _CLUSTER:
        return _cluster(descriptors, td, active)
    return None


def _array(
    descriptors: list[FlatTD], td: FlatTD, active: frozenset[int]
) -> LVType | None:
    if len(td.body) < 2:
        return None
    dimensions = int.from_bytes(td.body[:2], "big")
    element = read_var_size(td.body, 2 + 4 * dimensions)
    if dimensions < 1 or element is None:
        return None
    element_type = _build(descriptors, element[0], active)
    if element_type is None:
        return None
    return LVType(
        kind=LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=element_type,
        dimensions=dimensions,
    )


def _cluster(
    descriptors: list[FlatTD], td: FlatTD, active: frozenset[int]
) -> LVType | None:
    counted = read_var_size(td.body, 0)
    if counted is None:
        return None
    count, pos = counted
    fields: list[ClusterField] = []
    for number in range(count):
        member = read_var_size(td.body, pos)
        if member is None:
            return None
        pos = member[1]
        member_type = _build(descriptors, member[0], active)
        if member_type is None:
            return None
        fields.append(ClusterField(name=f"field_{number}", type=member_type))
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=fields or None,
    )
