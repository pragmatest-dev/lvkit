"""The writer of LabVIEW flattened data by a variant's type, that a generated
package's ``variants`` module holds, so Variant To Flattened String works on a
variant whose type is known only when the code runs.

It is the inverse of the reader in ``flat_reader.py`` and is appended after it in
the module, reading the same tables (``_FLAT_NUMBERS``, ``_number_name``) and the
same type reader (``read_type``): numbers and Booleans as ``struct`` data, a
string or array behind 32-bit sizes (an array's one per dimension, the slowest
varying first), a cluster as its members one after another, an enum as its
number, all big-endian. The ``type string`` output is the type string the
variant's type records. A type this does not write (an extended-precision number,
a refnum, a waveform, a path, a class) raises ``NotImplementedError`` naming it.
"""

from __future__ import annotations

_WRITER_SOURCE = '''

# The writer of flattened data by a variant's type (the inverse of the reader).
def _unwritable(lv_type: LVType) -> NotImplementedError:
    return NotImplementedError(
        f"flattened data of the type {lv_type.name!r} (descriptor code "
        f"{lv_type.code}) is not written"
    )


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
    (big-endian, sizes prepended)."""
    info = read_type(lv_type)
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
        members = list(value)
        if len(members) != len(info.fields):
            raise ValueError(
                f"a cluster of {len(info.fields)} members was given "
                f"{len(members)} values"
            )
        return b"".join(
            _flatten_value(item, member)
            for item, member in zip(members, info.fields)
        )
    raise _unwritable(lv_type)


def variant_to_flattened(variant: Variant) -> tuple[list[int], str]:
    """The type string (a list of 16-bit numbers) the variant's type records and
    its value flattened, as a ``latin-1`` string."""
    lv_type = variant.type
    if lv_type is None or lv_type.type_string is None:
        raise NotImplementedError(
            "Variant To Flattened String: the variant holds "
            + ("no type" if lv_type is None else repr(lv_type.name))
            + ", which records no type string"
        )
    data = _flatten_value(variant.value, lv_type)
    return list(lv_type.type_string), data.decode("latin-1")
'''


def flat_writer_source() -> str:
    """The writer's source."""
    return _WRITER_SOURCE
