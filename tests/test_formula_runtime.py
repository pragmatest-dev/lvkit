"""Tests for the Formula Node runtime helpers and emitted-function execution.

The Formula Node backend emits pure Python (no C compiler / FFI). LabVIEW
numeric semantics that Python doesn't reproduce natively — fixed-width
integer wrap, round-to-nearest-even on int assignment, C-style fmod/rem —
live as helpers in lvkit.runtime.lv. These tests pin those helpers and run a
representative emitted function end to end (scalar + array in/out).
"""

from __future__ import annotations

from lvkit.formula.emit import VarSpec, transpile
from lvkit.runtime import lv

# --- fixed-width integer coercion (round-to-nearest-even + wrap) -----------


def test_signed_width_wrap():
    assert lv.i16(40000) == -25536        # 40000 - 65536
    assert lv.i16(-1) == -1
    assert lv.i8(127) == 127
    assert lv.i8(128) == -128             # wraps past the signed max


def test_unsigned_width_wrap():
    assert lv.u8(300) == 44               # 300 % 256
    assert lv.u8(-1) == 255
    assert lv.u16(65536) == 0


def test_round_ties_to_even_on_store():
    # LabVIEW rounds a real into an integer terminal, ties to even.
    assert lv.i32(2.5) == 2
    assert lv.i32(3.5) == 4
    assert lv.i32(-2.5) == -2


def test_sign_fmod_rem():
    assert lv.sign(-3.2) == -1
    assert lv.sign(0) == 0
    assert lv.sign(5) == 1
    # C/LV fmod takes the sign of the dividend (Python % takes the divisor's)
    assert lv.fmod(-7.0, 3.0) == -1.0
    assert lv.rem(7.5, 2.0) == -0.5       # 7.5 - 2*round(3.75) = 7.5 - 8


# --- emitted function executes (scalar + array marshaling) -----------------


def test_scalar_and_array_round_trip():
    script = (
        "int32 i=0;\n"
        "y = a + 2**3;\n"
        "for (i=0; i<n; i++) out[i] = data[i]*2;\n"
    )
    variables = [
        VarSpec("a", "NumFloat64", "in", False),
        VarSpec("n", "NumInt32", "in", False),
        VarSpec("y", "NumFloat64", "out", False),
        VarSpec("data", "NumFloat64", "in", True),
        VarSpec("out", "NumFloat64", "inout", True),
    ]
    res = transpile(script, variables, func_name="formula_t")
    ns: dict = {}
    exec("import math\nfrom lvkit.runtime import lv as _lv\n" + res.source, ns)
    result = ns["formula_t"](a=5.0, n=3, data=[1.0, 2.0, 3.0], out=[0.0, 0.0, 0.0])

    assert result["y"] == 13.0                 # 5 + 2**3
    assert result["out"] == [2.0, 4.0, 6.0]    # data*2, written in place


def test_empty_array_input():
    # A zero-length array runs the loop zero times and reads back as [].
    script = "int32 i=0;\nfor (i=0; i<n; i++) out[i] = data[i]*2;\n"
    variables = [
        VarSpec("n", "NumInt32", "in", False),
        VarSpec("data", "NumFloat64", "in", True),
        VarSpec("out", "NumFloat64", "inout", True),
    ]
    res = transpile(script, variables, func_name="formula_e")
    ns: dict = {}
    exec("import math\nfrom lvkit.runtime import lv as _lv\n" + res.source, ns)
    assert ns["formula_e"](n=0, data=[], out=[])["out"] == []
