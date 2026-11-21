"""The reader of LabVIEW flattened data by a type known only when the code runs,
that a generated package's ``variants`` module holds, so Flattened String To
Variant works on a ``type string`` the diagram computes.

It reads the same format ``flat.py`` writes ``struct`` calls for when the type is
known as the code is generated (NI's "Flattened Data" page, quoted there): numbers
and Booleans as ``struct`` data, a string or array behind 32-bit sizes (an array's
one per dimension, the slowest varying first), a cluster as its members one after
another, an enum as its number. The tables it reads by (the ``struct`` code and
width of each number, the prefix a unit type swaps, the function that reads an
extended-precision number) are written into the module from ``flat_tables``, the
module ``flat.py`` reads them from, so the two cannot differ; the type is read
with ``read_type`` (``type_reader.py``). A time stamp is read by the function
``flat_timestamp_source`` holds, the one ``flat.py`` writes for a type known when
generating. A type this does not read (a refnum, a waveform, a path, a class: no
type string lvkit writes records them) raises ``NotImplementedError`` naming it.
A cluster is read as a tuple.
"""

from __future__ import annotations

from .flat_tables import (
    EXTENDED_UNFLATTEN,
    NUMBER_PREFIX,
    UNIT_PREFIX,
    number_formats,
)
from .flat_timestamp_source import LAZY_HIGHTIME_SETUP, UNFLATTEN_TIMESTAMP

_READER_TEMPLATE = '''

# The reader of flattened data by a type known when the code runs.
_FLAT_NUMBERS = $NUMBERS
_UNIT_PREFIX = $UNIT_PREFIX
_NUMBER_PREFIX = $NUMBER_PREFIX
_EXTENDED_NUMBERS = ("NumFloatExt", "NumComplexExt")


$EXTENDED_READER


$TIMESTAMP_READER


def _unreadable(lv_type: LVType) -> NotImplementedError:
    return NotImplementedError(
        f"flattened data of the type {lv_type.name!r} (descriptor code "
        f"{lv_type.code}) is not read"
    )


def _number_name(info: TypeInfo) -> str:
    name = info.number or ""
    if name.startswith(_UNIT_PREFIX):
        return _NUMBER_PREFIX + name[len(_UNIT_PREFIX):]
    return name


def _fixed_size(lv_type: LVType) -> int | None:
    """The flattened size in bytes of ``lv_type``, or None when it varies."""
    info = read_type(lv_type)
    if info.kind == "boolean":
        return 1
    if info.kind == "timestamp":
        return 16
    if info.kind in ("number", "enum"):
        name = _number_name(info)
        if name == "NumFloatExt":
            return 16
        if name == "NumComplexExt":
            return 32
        return _FLAT_NUMBERS[name][1] if name in _FLAT_NUMBERS else None
    if info.kind == "cluster":
        sizes = [_fixed_size(member) for member in info.fields]
        return None if None in sizes else sum(sizes)
    return None


def _reshape(items: list, sizes: list[int]) -> list:
    """``items`` as nested lists of the given ``sizes``, outermost first."""
    if len(sizes) == 1:
        return items
    stride = 1
    for size in sizes[1:]:
        stride *= size
    return [
        _reshape(items[i * stride:(i + 1) * stride], sizes[1:])
        for i in range(sizes[0])
    ]


def _unflatten_value(
    data: bytes, pos: int, lv_type: LVType, order: str, prefix: bool
) -> tuple[Any, int]:
    """The value of ``lv_type`` flattened in ``data`` at ``pos``, and the position
    after it. ``order`` is the byte-order prefix of a ``struct`` format; a string
    or array that is the whole value carries no size when ``prefix`` is false."""
    info = read_type(lv_type)
    if info.kind in ("number", "enum"):
        name = _number_name(info)
        if name in _EXTENDED_NUMBERS:
            value, pos = _read_extended(data, pos)
            if name == "NumComplexExt":
                imaginary, pos = _read_extended(data, pos)
                value = complex(value, imaginary)
            return value, pos
        if name not in _FLAT_NUMBERS:
            raise _unreadable(lv_type)
        code, width = _FLAT_NUMBERS[name]
        value = struct.unpack_from(order + code, data, pos)
        return (complex(*value) if len(code) == 2 else value[0]), pos + width
    if info.kind == "boolean":
        return struct.unpack_from(order + "?", data, pos)[0], pos + 1
    if info.kind == "timestamp":
        return _read_timestamp(data, pos, order)
    if info.kind == "string":
        if not prefix:
            return data[pos:].decode("latin-1"), len(data)
        count = struct.unpack_from(order + "i", data, pos)[0]
        pos += 4
        if count < 0 or pos + count > len(data):
            raise struct.error("a string runs past the end of the data")
        return data[pos:pos + count].decode("latin-1"), pos + count
    if info.kind == "array":
        dimensions = len(info.dimensions)
        element = info.element
        if prefix or dimensions > 1:
            sizes = list(
                struct.unpack_from(order + "i" * dimensions, data, pos)
            )
            pos += 4 * dimensions
        else:
            width = _fixed_size(element)
            if not width:
                raise NotImplementedError(
                    "flattened data of an array of variable-size elements "
                    f"({getattr(element, 'kind', element)!r}) without sizes is "
                    "not read"
                )
            sizes = [(len(data) - pos) // width]
        count = 1
        for size in sizes:
            count *= size
        items = []
        for _ in range(count):
            item, pos = _unflatten_value(data, pos, element, order, True)
            items.append(item)
        return _reshape(items, sizes), pos
    if info.kind == "cluster":
        values = []
        for member in info.fields:
            value, pos = _unflatten_value(data, pos, member, order, True)
            values.append(value)
        return tuple(values), pos
    raise _unreadable(lv_type)


def unflatten_data(
    data: bytes, lv_type: LVType, order: str = ">", prefix: bool = True
) -> Any:
    """The value of ``lv_type`` that ``data`` holds in LabVIEW's flattened form
    (``order`` is ``>``, ``=`` or ``<``)."""
    return _unflatten_value(data, 0, lv_type, order, prefix)[0]


def type_from_string(type_string: Any) -> LVType:
    """The ``LVType`` a type string (a list of 16-bit numbers) describes."""
    words = [w & 0xFFFF for w in type_string]
    if len(words) < 2 or words[0] != 2 * len(words):
        raise ValueError(
            f"the type string has a size of {words[0] if words else 0} bytes "
            f"it cannot hold in {len(words)} words"
        )
    return _member(words, 0)[0]


def variant_from_flattened(data: str, type_string: Any) -> Variant:
    """A variant of the type ``type_string`` describes, holding the value the
    flattened string ``data`` has (big-endian, sizes prepended)."""
    lv_type = type_from_string(type_string)
    return Variant(unflatten_data(data.encode("latin-1"), lv_type), lv_type)
'''


def flat_reader_source() -> str:
    """The reader's source, its tables written from ``flat_tables``."""
    numbers = {name: pair for name, pair in sorted(number_formats().items())}
    substitutions = {
        "NUMBERS": repr(numbers),
        "UNIT_PREFIX": repr(UNIT_PREFIX),
        "NUMBER_PREFIX": repr(NUMBER_PREFIX),
        "EXTENDED_READER": EXTENDED_UNFLATTEN.format(name="_read_extended").rstrip(),
        "TIMESTAMP_READER": UNFLATTEN_TIMESTAMP.format(
            name="_read_timestamp",
            order="order",
            params=", order",
            setup=LAZY_HIGHTIME_SETUP,
        ).rstrip(),
    }
    text = _READER_TEMPLATE
    for key, value in sorted(substitutions.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(f"${key}", value)
    return text
