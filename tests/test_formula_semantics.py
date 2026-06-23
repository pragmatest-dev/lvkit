"""Locked LabVIEW Formula Node numeric-semantics regression.

Ground truth: a real Formula Node run reported by @Himmelt on
github.com/pragmatest-dev/lvkit issues/8 (the probe in
docs/formula_semantics_probe.md). Each case below transpiles a tiny
script, EXECUTES the emitted Python, and asserts the value LabVIEW
produced — pinning the numeric model the NI docs leave underspecified.

Covers only the constructs the backend supports today and whose oracle
result is consistent. Deliberately NOT locked here (tracked separately):
  * `~`/negative-operand bitwise: LV returned INT32_MAX for `~0`, `~5`,
    `-1 & 255` — anomalous vs two's complement; needs interpretation.
  * getexp/getman/rand/sizeOfDim and 2D indexing: not yet supported
    (fail loud).
  * div-by-zero / domain edges (Script 2): LV yields inf/nan, the pure
    Python backend currently raises — a separate decision.
"""

from __future__ import annotations

import math

import pytest

from lvkit.formula.emit import VarSpec, transpile
from lvkit.runtime import lv as _lv


def _run(script: str, variables: list[VarSpec], **inputs):
    res = transpile(script, variables)
    ns: dict = {"_lv": _lv, "math": math}
    exec(res.source, ns)
    return ns["formula"](**inputs)


# Int32 inout array RI, float64 inout array RF, double input array A, n int32.
_VARS = [
    VarSpec("RI", "NumInt32", "inout", True),
    VarSpec("RF", "NumFloat64", "inout", True),
    VarSpec("A", "NumFloat64", "in", True),
    VarSpec("n", "NumInt32", "in", False),
]

# (script line, expected int32 result) — oracle RI rows, anomalies excluded.
_INT_CASES = [
    # round-to-nearest-even when a real is stored into an int terminal
    ("RI[0] = 0.5;", 0), ("RI[0] = 1.5;", 2), ("RI[0] = 2.5;", 2),
    ("RI[0] = 3.5;", 4), ("RI[0] = -2.5;", -2),
    # fixed-width integer wrap on a typed local, then read back
    ("uInt8 u; u = 300; RI[0] = u;", 44),
    ("uInt8 u; u = -1; RI[0] = u;", 255),
    ("int8 i; i = 200; RI[0] = i;", -56),
    ("uInt16 u; u = 70000; RI[0] = u;", 4464),
    ("int16 i; i = 40000; RI[0] = i;", -25536),
    ("uInt32 u; u = 5000000000; RI[0] = u;", 705032704),
    # arithmetic promotes — int16 30000+30000 does NOT wrap at 16 bits
    ("int16 a; int16 b; a = 30000; b = 30000; RI[0] = a + b;", 60000),
    # `/` is always real division; the int store rounds (ties to even)
    ("RI[0] = 7 / 2;", 4), ("RI[0] = -7 / 2;", -4), ("RI[0] = 5 / 2;", 2),
    # `%` is truncated (sign of dividend)
    ("RI[0] = -7 % 3;", -1), ("RI[0] = 7 % -3;", 1),
    # bitwise (non-anomalous rows)
    ("RI[0] = 12 & 10;", 8), ("RI[0] = 12 | 10;", 14), ("RI[0] = 12 ^ 10;", 6),
    ("RI[0] = 1 << 4;", 16), ("RI[0] = 1 << 31;", -2147483648),
    ("RI[0] = -8 >> 1;", -4), ("RI[0] = 256 >> 2;", 64),
    # comparisons + logical yield 1/0
    ("RI[0] = (3 > 2);", 1), ("RI[0] = (2 > 3);", 0),
    ("RI[0] = (5 == 5);", 1), ("RI[0] = (3 <= 3);", 1),
    ("RI[0] = (5 && 3);", 1), ("RI[0] = (5 && 0);", 0),
    ("RI[0] = (0 || 5);", 1), ("RI[0] = (0 || 0);", 0),
    ("RI[0] = !5;", 0), ("RI[0] = !0;", 1), ("RI[0] = !!5;", 1),
    # power: right-assoc, binds tighter than unary minus; integer powers
    ("RI[0] = 2 ** 3;", 8), ("RI[0] = (-2) ** 2;", 4), ("RI[0] = (-2) ** 3;", -8),
    ("RI[0] = 2 ** 3 ** 2;", 512), ("RI[0] = -2 ** 2;", -4),
    # precedence: * over +, + over <<, left-assoc subtraction
    ("RI[0] = 1 + 2 * 3;", 7), ("RI[0] = 1 << 2 + 1;", 8),
    ("RI[0] = 20 - 5 - 3;", 12),
    ("RI[0] = abs(-3);", 3), ("RI[0] = sign(-3);", -1), ("RI[0] = sign(0);", 0),
]

# (script line, expected float result) — oracle RF rows.
_FLOAT_CASES = [
    ("RF[0] = 7 / 2;", 3.5), ("RF[0] = 1 / 3;", 1 / 3),
    ("RF[0] = 2 ** -1;", 0.5), ("RF[0] = 2 ** 0.5;", math.sqrt(2)),
    ("RF[0] = mod(-7, 3);", 2.0), ("RF[0] = mod(7.5, 2);", 1.5),
    ("RF[0] = rem(7, 3);", 1.0), ("RF[0] = rem(-7, 3);", -1.0),
    ("RF[0] = sin(pi / 2);", 1.0), ("RF[0] = log(100.0);", 2.0),
    ("RF[0] = ln(2.718281828459045);", 1.0), ("RF[0] = log2(8.0);", 3.0),
    ("RF[0] = sqrt(2.0);", math.sqrt(2)), ("RF[0] = abs(-2.5);", 2.5),
    ("RF[0] = int(2.5);", 2.0), ("RF[0] = int(-2.5);", -2.0),
    ("RF[0] = intrz(2.7);", 2.0), ("RF[0] = intrz(-2.7);", -2.0),
    ("RF[0] = ceil(2.1);", 3.0), ("RF[0] = floor(-2.1);", -3.0),
    ("RF[0] = max(2.0, 5.0);", 5.0), ("RF[0] = min(2.0, 5.0);", 2.0),
    ("RF[0] = cot(1.0);", 1.0 / math.tan(1.0)),
    ("RF[0] = sinc(1.0);", math.sin(1.0)),
    ("RF[0] = pi;", math.pi),
    ("RF[0] = A[0];", 10.0), ("RF[0] = A[n - 1];", 50.0),
]


@pytest.mark.parametrize("script, expected", _INT_CASES)
def test_integer_semantics(script, expected):
    out = _run(script, _VARS, RI=[0] * 4, RF=[0.0], A=[10.0, 20, 30, 40, 50], n=5)
    assert out["RI"][0] == expected, script


@pytest.mark.parametrize("script, expected", _FLOAT_CASES)
def test_float_semantics(script, expected):
    out = _run(script, _VARS, RI=[0] * 4, RF=[0.0], A=[10.0, 20, 30, 40, 50], n=5)
    assert out["RF"][0] == pytest.approx(expected, rel=1e-9, abs=1e-9), script


# A Formula Node has no error terminal, so domain/zero errors coerce to IEEE
# inf/nan rather than raising (oracle Script 2). The pure-Python backend
# routes these through non-raising _lv.* helpers.
_EDGE_CASES = [
    ("RF[0] = 1 / 0;", math.inf), ("RF[0] = 1.0 / 0.0;", math.inf),
    ("RF[0] = -1.0 / 0.0;", -math.inf), ("RF[0] = 0.0 / 0.0;", math.nan),
    ("RF[0] = sqrt(-1.0);", math.nan), ("RF[0] = ln(0.0);", -math.inf),
    ("RF[0] = ln(-1.0);", math.nan), ("RF[0] = log(0.0);", -math.inf),
    ("RF[0] = acos(2.0);", math.nan), ("RF[0] = asin(2.0);", math.nan),
    ("RF[0] = (-8) ** (1.0 / 3.0);", math.nan), ("RF[0] = 0 ** 0;", 1.0),
    # getexp/getman: mantissa in [1, 2), x == getman(x) * 2**getexp(x).
    ("RF[0] = getexp(12.0);", 3.0), ("RF[0] = getman(12.0);", 1.5),
    ("RF[0] = getexp(0.75);", -1.0), ("RF[0] = getman(0.75);", 1.5),
]


@pytest.mark.parametrize("script, expected", _EDGE_CASES)
def test_no_error_terminal_coercion(script, expected):
    out = _run(script, _VARS, RI=[0] * 4, RF=[0.0], A=[10.0, 20, 30, 40, 50], n=5)
    got = out["RF"][0]
    if math.isnan(expected):
        assert isinstance(got, float) and math.isnan(got), script
    else:
        assert got == expected, script


def test_division_wraps_only_ambiguous_divisors():
    # A nonzero literal divisor can't trap -> keep the plain operator; a
    # variable (or expression) divisor routes through the inf/nan helper.
    src = transpile("y = a / 2; z = a / b;", [
        VarSpec("y", "NumFloat64", "out", False),
        VarSpec("z", "NumFloat64", "out", False),
        VarSpec("a", "NumFloat64", "in", False),
        VarSpec("b", "NumFloat64", "in", False),
    ]).source
    assert "a / 2" in src
    assert "_lv.div(a, b)" in src


def test_power_wraps_only_non_literal_base():
    # ``2 ** n`` can't produce a complex result -> plain operator; a base that
    # might be negative routes through _lv.powf.
    src = transpile("y = 2 ** n; z = b ** 0.5;", [
        VarSpec("y", "NumFloat64", "out", False),
        VarSpec("z", "NumFloat64", "out", False),
        VarSpec("n", "NumInt32", "in", False),
        VarSpec("b", "NumFloat64", "in", False),
    ]).source
    assert "2 ** n" in src
    assert "_lv.powf(b, 0.5)" in src
