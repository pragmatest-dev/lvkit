"""Element-wise numeric helpers for LabVIEW polymorphic primitives.

LabVIEW numeric functions (Add, Subtract, Sign, comparisons, …) operate
element-wise on arrays, broadcasting a scalar against an array. Generated
code routes a numeric primitive through these helpers only when one of its
operands is an array; pure-scalar uses keep the plain operator and never
touch this module.

Arrays are Python lists (lvkit's array representation); these helpers do not
change that. Nested arrays broadcast recursively, matching LabVIEW's
element-wise behaviour over multidimensional arrays.
"""

from __future__ import annotations

import math as _math
import operator as _op
import random as _random
from collections.abc import Callable


def _binop(a, b, f: Callable):
    a_arr, b_arr = isinstance(a, list), isinstance(b, list)
    if a_arr and b_arr:
        return [_binop(x, y, f) for x, y in zip(a, b)]
    if a_arr:
        return [_binop(x, b, f) for x in a]
    if b_arr:
        return [_binop(a, y, f) for y in b]
    return f(a, b)


def _unop(a, f: Callable):
    return [_unop(x, f) for x in a] if isinstance(a, list) else f(a)


def add(a, b):
    return _binop(a, b, _op.add)


def sub(a, b):
    return _binop(a, b, _op.sub)


def mul(a, b):
    return _binop(a, b, _op.mul)


def truediv(a, b):
    return _binop(a, b, _op.truediv)


def floordiv(a, b):
    return _binop(a, b, _op.floordiv)


def mod(a, b):
    return _binop(a, b, _op.mod)


def pow_(a, b):
    return _binop(a, b, _op.pow)


def gt(a, b):
    return _binop(a, b, _op.gt)


def lt(a, b):
    return _binop(a, b, _op.lt)


def ge(a, b):
    return _binop(a, b, _op.ge)


def le(a, b):
    return _binop(a, b, _op.le)


def eq(a, b):
    return _binop(a, b, _op.eq)


def ne(a, b):
    return _binop(a, b, _op.ne)


def neg(a):
    return _unop(a, _op.neg)


# --- Formula Node scalar helpers (LabVIEW C-like numeric semantics) ---------
#
# Used by Python emitted from a Formula Node script (see formula/emit.py).
# Integer terminals/locals have a fixed width: assigning a real rounds to
# nearest (ties to even) and the value wraps within the declared width. These
# reproduce that without a C compiler.


def _int_store(x, bits: int, signed: bool):
    """Round-to-nearest-even, then wrap to a fixed-width integer."""
    x = round(x) & ((1 << bits) - 1)  # round() ties-to-even; then mask
    if signed and x >= (1 << (bits - 1)):
        x -= 1 << bits
    return x


def i8(x):
    return _int_store(x, 8, True)


def i16(x):
    return _int_store(x, 16, True)


def i32(x):
    return _int_store(x, 32, True)


def i64(x):
    return _int_store(x, 64, True)


def u8(x):
    return _int_store(x, 8, False)


def u16(x):
    return _int_store(x, 16, False)


def u32(x):
    return _int_store(x, 32, False)


def u64(x):
    return _int_store(x, 64, False)


def sign(x):
    return (x > 0) - (x < 0)


def rem(a, b):
    """Truncated remainder — LabVIEW ``%`` operator and ``rem()``. Sign
    follows the dividend (``-7 rem 3 == -1``). Integer-exact for ints."""
    if b == 0:
        return _math.nan
    if isinstance(a, int) and isinstance(b, int):
        q = abs(a) // abs(b)
        r = abs(a) - q * abs(b)
        return -r if a < 0 else r
    return _math.fmod(a, b)


def lvmod(a, b):
    """Floored modulo — LabVIEW ``mod()``. Sign follows the divisor
    (``mod(-7, 3) == 2``)."""
    if b == 0:
        return _math.nan
    if isinstance(a, int) and isinstance(b, int):
        return a % b  # Python ``%`` is floored
    return a - b * _math.floor(a / b)


# --- non-raising math (a Formula Node has no error terminal, so LabVIEW
# coerces domain/zero errors to IEEE inf/nan instead of trapping) -----------


def div(a, b):
    """Real division that yields IEEE inf/nan on a zero divisor instead of
    raising (``1/0 -> inf``, ``-1/0 -> -inf``, ``0/0 -> nan``)."""
    if b != 0:
        return a / b
    if a == 0:
        return _math.nan
    return _math.copysign(_math.inf, a) * _math.copysign(1.0, b)


def powf(a, b):
    """Power with LabVIEW edge semantics: a negative base raised to a
    non-integer exponent is ``nan`` (not a complex number); ``0**0 == 1``."""
    if a < 0 and b != _math.floor(b):
        return _math.nan
    try:
        return _math.pow(a, b)
    except (ValueError, OverflowError):
        return _math.nan


def sqrt(x):
    return _math.sqrt(x) if x >= 0 else _math.nan


def ln(x):
    if x > 0:
        return _math.log(x)
    return -_math.inf if x == 0 else _math.nan


def log10(x):
    if x > 0:
        return _math.log10(x)
    return -_math.inf if x == 0 else _math.nan


def log2(x):
    if x > 0:
        return _math.log2(x)
    return -_math.inf if x == 0 else _math.nan


def asin(x):
    return _math.asin(x) if -1 <= x <= 1 else _math.nan


def acos(x):
    return _math.acos(x) if -1 <= x <= 1 else _math.nan


def acosh(x):
    return _math.acosh(x) if x >= 1 else _math.nan


def atanh(x):
    if -1 < x < 1:
        return _math.atanh(x)
    if x == 1:
        return _math.inf
    if x == -1:
        return -_math.inf
    return _math.nan


def getexp(x):
    """Binary exponent e where ``x == getman(x) * 2**e`` and the mantissa is
    in [1, 2) — LabVIEW ``getexp`` (e.g. ``getexp(12) == 3``)."""
    if x == 0:
        return 0.0
    _, e = _math.frexp(x)  # frexp mantissa is in [0.5, 1)
    return float(e - 1)


def getman(x):
    """Binary mantissa in [1, 2) — LabVIEW ``getman`` (``getman(12) == 1.5``)."""
    if x == 0:
        return 0.0
    m, _ = _math.frexp(x)
    return m * 2.0


def rand():
    """Uniform pseudo-random float in [0, 1) — LabVIEW ``rand()``. The value
    is intentionally non-deterministic at runtime (codegen stays stable)."""
    return _random.random()


def size_of_dim(arr, dim):
    """Length of array ``arr`` along dimension ``dim`` — LabVIEW
    ``sizeOfDim``. Dim 0 is the outermost (``len(arr)``); deeper dims descend
    one nesting level each. Out-of-range / ragged access returns 0."""
    cur = arr
    for _ in range(dim):
        if not isinstance(cur, list) or not cur:
            return 0
        cur = cur[0]
    return len(cur) if isinstance(cur, list) else 0


def land(a, b):
    return 1 if (a and b) else 0  # && yields 1/0, not operand


def lor(a, b):
    return 1 if (a or b) else 0  # || yields 1/0, not operand


def lnot(a):
    return 0 if a else 1


def cot(x):
    return 1.0 / _math.tan(x)


def csc(x):
    return 1.0 / _math.sin(x)


def sec(x):
    return 1.0 / _math.cos(x)


def sinc(x):
    return 1.0 if x == 0 else _math.sin(x) / x
