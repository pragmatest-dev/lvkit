"""Python source text for numeric conversions that Python's operators do not do.

Each function takes operand source text and returns an expression. The operands
are expressions without side effects, so an expression may repeat them.
"""

from __future__ import annotations

# The ``ctypes`` integer type of each LabVIEW integer type name, and its width
# in bits.
CTYPES_INTEGER = {
    "NumInt8": ("c_int8", 8),
    "NumUInt8": ("c_uint8", 8),
    "NumInt16": ("c_int16", 16),
    "NumUInt16": ("c_uint16", 16),
    "NumInt32": ("c_int32", 32),
    "NumUInt32": ("c_uint32", 32),
    "NumInt64": ("c_int64", 64),
    "NumUInt64": ("c_uint64", 64),
}


def fixed_width(expression: str, underlying: str) -> str:
    """``expression`` as the fixed-width integer ``underlying`` names: a value
    outside the width wraps around."""
    return f"ctypes.{CTYPES_INTEGER[underlying][0]}({expression}).value"


def float_divide(dividend: str, divisor: str) -> str:
    """Floating-point division with IEEE results at a zero divisor: a signed
    infinity, or NaN for zero over zero, where ``/`` would raise."""
    return (
        f"({dividend} / {divisor} if {divisor} else math.nan "
        f"if {dividend} == 0 or math.isnan({dividend}) "
        f"else math.copysign(math.inf, {dividend}) * math.copysign(1.0, {divisor}))"
    )
