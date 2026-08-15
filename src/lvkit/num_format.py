"""Numeric-constant display formatting, shared by the renderer and the
graph/netlist text output so hex/octal/binary radix and float precision render
identically everywhere.

Kept import-neutral (depends only on ``models``) so ``graph/`` can use it
without importing ``render/`` — the reason ``describe``/netlist historically
lost the radix while ``render`` kept it.
"""

from __future__ import annotations

import re

from .models import LVType, LVTypeKind

# A numeric constant's DCO display-format is a printf-ish spec. We only handle
# the plain forms (a precision and a conversion letter); anything more exotic
# (e.g. ``%#_13g`` seen once in the corpus with a non-numeric width token) is
# left for the caller to fall back on default formatting rather than guess at.
_NUMERIC_FORMAT_RE = re.compile(
    r"^%[#0\- +]*\d*\.(?P<prec>\d+)(?P<conv>[fFeEgGxXob])$"
)
# LabVIEW prefixes a non-decimal numeric constant with a lowercase letter —
# "x" for hex, "o" for octal, "b" for binary — never a "0x"/"0o"/"0b" style
# prefix (verified against the task's own example: U8 31 -> "x1F").
_RADIX_PREFIX = {"x": "x", "X": "x", "o": "o", "b": "b"}
_RADIX_FORMAT_SPEC = {"x": "X", "X": "X", "o": "o", "b": "b"}

_INT_BYTE_WIDTH = {
    "NumInt8": 1, "NumUInt8": 1,
    "NumInt16": 2, "NumUInt16": 2,
    "NumInt32": 4, "NumUInt32": 4,
    "NumInt64": 8, "NumUInt64": 8,
}


def int_byte_width(lv_type: LVType | None) -> int | None:
    """Byte width of an integer type (1/2/4/8), or None if not a plain
    fixed-width integer (float, complex, non-numeric, or unresolved type).
    Used to two's-complement a negative value to its type's bit width
    before hex/octal/binary display — LabVIEW shows e.g. I16 -1 as
    ``xFFFF``, not a Python-style ``-x1``."""
    if lv_type is None or lv_type.kind != LVTypeKind.PRIMITIVE:
        return None
    return _INT_BYTE_WIDTH.get(lv_type.underlying_type or "")


def format_numeric_const(
    lv_type: LVType | None, value: object, display_format: str | None,
) -> str | None:
    """Apply a numeric constant's DCO-provided display-format string to its
    decoded value: hex/octal/binary radix (with LabVIEW's lowercase x/o/b
    prefix, negative values two's-complemented to the type's bit width) or
    float precision (``%.Nf``/``%.Ng``/``%.Ne`` -> N digits).

    Returns None — caller falls back to the default decimal display —
    when there's no format string, or it doesn't match the plain printf
    spec this function understands (see ``_NUMERIC_FORMAT_RE``)."""
    if not display_format:
        return None
    m = _NUMERIC_FORMAT_RE.match(display_format)
    if not m:
        return None
    conv = m.group("conv")
    prec = int(m.group("prec"))
    try:
        fval = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

    if conv in _RADIX_PREFIX:
        ival = int(fval)
        width = int_byte_width(lv_type)
        if ival < 0:
            if width is None:
                # Can't two's-complement without a known bit width — don't
                # guess a width, fall back to default formatting instead.
                return None
            ival &= (1 << (width * 8)) - 1
        digits = format(ival, _RADIX_FORMAT_SPEC[conv])
        if len(digits) < prec:
            digits = digits.rjust(prec, "0")
        return _RADIX_PREFIX[conv] + digits

    # f/F/e/E/g/G — printf precision digits; Python's format mini-language
    # uses the identical conversion letters and semantics.
    return format(fval, f".{prec}{conv}")
