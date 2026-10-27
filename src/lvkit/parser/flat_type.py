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

from lvkit.lv_type_codes import TD_FULL_TYPE, plain_number_name
from lvkit.models import ClusterField, LVType, LVTypeKind

from .td_pool import RawTD, TdPool, read_var_size

_NUMERIC_TYPES = frozenset(
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
        # Physical quantities flatten as the float they wrap.
        TD_FULL_TYPE.UnitFloat32,
        TD_FULL_TYPE.UnitFloat64,
        TD_FULL_TYPE.UnitFloatExt,
        TD_FULL_TYPE.UnitComplex64,
        TD_FULL_TYPE.UnitComplex128,
        TD_FULL_TYPE.UnitComplexExt,
    }
)
_ENUM_TYPES = frozenset(
    {TD_FULL_TYPE.UnitUInt8, TD_FULL_TYPE.UnitUInt16, TD_FULL_TYPE.UnitUInt32}
)
_PRIMITIVE_TYPES = frozenset(
    {
        TD_FULL_TYPE.Void,
        TD_FULL_TYPE.Boolean,
        TD_FULL_TYPE.String,
        TD_FULL_TYPE.Path,
        TD_FULL_TYPE.Picture,
        TD_FULL_TYPE.LVVariant,
    }
)


@dataclass(frozen=True)
class FlatTD:
    """One flattened type descriptor: its type code, its type-specific bytes
    (everything after the 4-byte header, up to the descriptor's size) and its
    label, when its flags say it has one and its type's layout is read."""

    type_code: int
    body: bytes
    label: str | None = None


def read_descriptors(
    data: bytes | memoryview, pos: int, count: int
) -> tuple[list[FlatTD], int] | None:
    """``count`` consecutive descriptors starting at ``pos``; the list and the
    position after the last one, or None when the data ends first."""
    raw: list[RawTD] = []
    for _ in range(count):
        size_field = read_var_size(data, pos)
        if size_field is None:
            return None
        size, body_start = size_field
        header_len = body_start - pos + 2
        end = pos + size
        if size < header_len or end > len(data):
            return None
        raw.append(
            RawTD(
                type_code=data[body_start + 1],
                flags=data[body_start],
                body=bytes(data[body_start + 2 : end]),
            )
        )
        pos = end
    names = TdPool(raw).names
    return [
        FlatTD(td.type_code, td.body, label) for td, label in zip(raw, names)
    ], pos


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
    try:
        full_type = TD_FULL_TYPE(td.type_code)
    except ValueError:
        return None
    active = active | {index}
    if full_type in _NUMERIC_TYPES:
        return LVType(
            kind=LVTypeKind.PRIMITIVE, underlying_type=plain_number_name(full_type.name)
        )
    if full_type in _ENUM_TYPES:
        return LVType(kind=LVTypeKind.ENUM, underlying_type=full_type.name)
    if full_type in _PRIMITIVE_TYPES:
        return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=full_type.name)
    if full_type == TD_FULL_TYPE.Array:
        return _array(descriptors, td, active)
    if full_type == TD_FULL_TYPE.Cluster:
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
        label = descriptors[member[0]].label
        fields.append(ClusterField(name=label or f"field_{number}", type=member_type))
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=fields or None,
    )
