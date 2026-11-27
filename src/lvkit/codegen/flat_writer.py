"""The writer of LabVIEW flattened data by a variant's type, that a generated
package's ``variants`` module holds, so Variant To Flattened String works on a
variant whose type is known only when the code runs.

It is the inverse of the reader in ``flat_reader.py`` and is appended after it in
the module, reading the same tables (``_FLAT_NUMBERS``, ``_number_name``) and the
same type reader (``read_type``): numbers and Booleans as ``struct`` data, a
string or array behind 32-bit sizes (an array's one per dimension, the slowest
varying first), a cluster as its members one after another, an enum as its
number, a time stamp as the function ``flat_timestamp_source`` holds, all
big-endian. The ``type string`` output is the type string the variant's type
records. An object of a class is written by ``flatten_class`` of the package's
``lv_classes`` module (``classes_module.py``). A type this does not write (an
extended-precision number, another refnum, a waveform, a path) raises
``NotImplementedError`` naming it.
"""

from __future__ import annotations

from .flat_timestamp_source import FLATTEN_TIMESTAMP, LAZY_HIGHTIME_SETUP

_WRITER_SOURCE = '''

# The writer of flattened data by a variant's type (the inverse of the reader).
$TIMESTAMP_WRITER


def _unwritable(lv_type: LVType) -> NotImplementedError:
    return NotImplementedError(
        f"flattened data of the type {lv_type.name!r} (descriptor code "
        f"{lv_type.code}) is not written"
    )


def cluster_members(value: Any) -> list:
    """The members of the cluster value ``value`` in order: those of a tuple or
    list; an error cluster's value is a LabVIEWError, whose members are its
    status, code and source."""
    if hasattr(value, "status") and hasattr(value, "code"):
        return [value.status, value.code, value.source]
    return list(value)


def _array_sizes(value: Any, dimensions: int) -> list[int]:
    """The size of each of ``dimensions`` nested lists of ``value``, outermost
    first (0 below an empty list)."""
    sizes = []
    for _ in range(dimensions):
        sizes.append(len(value))
        value = value[0] if len(value) else []
    return sizes


def _array_items(value: Any, dimensions: int) -> list:
    """The elements of ``dimensions`` nested lists, row by row."""
    if dimensions == 1:
        return list(value)
    return [item for row in value for item in _array_items(row, dimensions - 1)]


def _flatten_value(value: Any, lv_type: LVType) -> bytes:
    """The bytes of ``value`` of the type ``lv_type`` in LabVIEW's flattened form
    (big-endian, sizes prepended). A variant that holds no value (``None``) is
    the type's default: zero, False, an empty string or array, a cluster of
    defaults."""
    info = read_type(lv_type)
    if value is None and info.kind in ("number", "enum"):
        value = 0
    elif value is None and info.kind == "boolean":
        value = False
    elif value is None and info.kind == "string":
        value = ""
    elif value is None and info.kind == "array":
        value = []
    elif value is None and info.kind == "cluster":
        value = [None] * len(info.fields)
    if info.kind == "timestamp":
        # The default time stamp is 1904-01-01 00:00:00 UTC: zero seconds.
        return bytes(16) if value is None else _flatten_timestamp(value, ">")
    if info.kind in ("number", "enum"):
        name = _number_name(info)
        if name not in _FLAT_NUMBERS:
            raise _unwritable(lv_type)
        code = _FLAT_NUMBERS[name][0]
        if len(code) == 2:
            return struct.pack(">" + code, value.real, value.imag)
        return struct.pack(">" + code, value)
    if info.kind == "boolean":
        return struct.pack(">?", bool(value))
    if info.kind == "string":
        data = value if isinstance(value, bytes) else str(value).encode("latin-1")
        return struct.pack(">i", len(data)) + data
    if info.kind == "array":
        dimensions = len(info.dimensions)
        sizes = _array_sizes(value, dimensions)
        parts = [struct.pack(">" + "i" * dimensions, *sizes)]
        for item in _array_items(value, dimensions):
            parts.append(_flatten_value(item, info.element))
        return b"".join(parts)
    if info.kind == "cluster":
        members = cluster_members(value)
        if len(members) != len(info.fields):
            raise ValueError(
                f"a cluster of {len(info.fields)} members was given "
                f"{len(members)} values"
            )
        return b"".join(
            _flatten_value(item, member)
            for item, member in zip(members, info.fields)
        )
    is_class = info.kind == "refnum" and info.refnum == _CLASS_REFNUM_KIND
    if is_class and value is not None:
        # An object of a class is flattened by its own class (the package's
        # ``lv_classes`` module).
        return importlib.import_module(".lv_classes", __package__).flatten_class(value)
    raise _unwritable(lv_type)


def _labelled(numbers: tuple[int, ...], label: str) -> list[int]:
    """The type string ``numbers`` ending in ``label``: the name flag set in the
    type code, the label after the descriptor as a Pascal string padded to an even
    number of bytes, and the size word counting it."""
    raw = label.encode("latin-1")
    if len(raw) > 0xFF:
        raise ValueError("a label longer than 255 bytes")
    text = bytes([len(raw)]) + raw
    if len(text) % 2:
        text += b"\\x00"
    words = [w & 0xFFFF for w in numbers]
    words[0] += len(text)
    words[1] |= _NAME_FLAG
    words += struct.unpack(f">{len(text) // 2}H", text)
    return [w - 0x10000 if w & 0x8000 else w for w in words]


def variant_to_flattened(variant: Variant) -> tuple[list[int], str]:
    """The type string (a list of 16-bit numbers) the variant's type records, ending
    in the variant's name when it has one, and its value flattened, as a ``latin-1``
    string."""
    lv_type = variant.type
    if lv_type is None or lv_type.type_string is None:
        raise NotImplementedError(
            "Variant To Flattened String: the variant holds "
            + ("no type" if lv_type is None else repr(lv_type.name))
            + ", which records no type string"
        )
    data = _flatten_value(variant.value, lv_type)
    if variant.name:
        return _labelled(lv_type.type_string, variant.name), data.decode("latin-1")
    return list(lv_type.type_string), data.decode("latin-1")
'''


def flat_writer_source() -> str:
    """The writer's source, the name flag written from the encoder's."""
    timestamp_writer = FLATTEN_TIMESTAMP.format(
        name="_flatten_timestamp",
        order="order",
        params=", order",
        setup=LAZY_HIGHTIME_SETUP,
    ).rstrip()
    return _WRITER_SOURCE.replace("$TIMESTAMP_WRITER", timestamp_writer)
