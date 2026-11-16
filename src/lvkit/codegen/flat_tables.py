"""What the flattened-data format has in fixed tables, in one place for the code
that writes ``struct`` calls for a type known when generating (``flat.py``) and the
reader of flattened data by a type known only when the code runs (``flat_reader``,
written into the generated ``variants`` module): the ``struct`` code and width of
each number, the prefix a unit type swaps for its number's, and the function that
reads an extended-precision number. This module imports nothing from the package,
so both can import it."""

from __future__ import annotations

# The ``struct`` code, the ``ctypes`` type and the width in bytes of each integer.
INTEGER_CODES = {
    "NumInt8": ("b", "c_int8", 1),
    "NumUInt8": ("B", "c_uint8", 1),
    "NumInt16": ("h", "c_int16", 2),
    "NumUInt16": ("H", "c_uint16", 2),
    "NumInt32": ("i", "c_int32", 4),
    "NumUInt32": ("I", "c_uint32", 4),
    "NumInt64": ("q", "c_int64", 8),
    "NumUInt64": ("Q", "c_uint64", 8),
}
FLOAT_CODES = {"NumFloat32": ("f", 4), "NumFloat64": ("d", 8)}
COMPLEX_CODES = {"NumComplex64": ("ff", 8), "NumComplex128": ("dd", 16)}

# A unit type (an enum's, a physical quantity's) flattens as the number of the
# same name with this prefix swapped.
UNIT_PREFIX = "Unit"
NUMBER_PREFIX = "Num"


def number_formats() -> dict[str, tuple[str, int]]:
    """The ``struct`` code and width in bytes of each number type that flattens as
    a fixed run of ``struct`` data (integers, floating-point and complex numbers),
    by type name."""
    table = {name: (code, width) for name, (code, _, width) in INTEGER_CODES.items()}
    table.update(FLOAT_CODES)
    table.update(COMPLEX_CODES)
    return table


# The 128-bit IEEE form of an extended-precision number, read by the function
# ``def {name}(data, pos)``, which returns ``(value, pos)``.
EXTENDED_UNFLATTEN = """def {name}(data, pos):
    head = struct.unpack_from('>H', data, pos)[0]
    fraction = int.from_bytes(data[pos + 2:pos + 16], 'big')
    sign = -1.0 if head & 0x8000 else 1.0
    exponent = head & 0x7FFF
    if exponent == 0x7FFF:
        return (math.nan if fraction else sign * math.inf), pos + 16
    if exponent == 0:
        return sign * 0.0, pos + 16
    try:
        value = math.ldexp(fraction + (1 << 112), exponent - 16383 - 112)
    except OverflowError:
        value = math.inf
    return sign * value, pos + 16
"""
