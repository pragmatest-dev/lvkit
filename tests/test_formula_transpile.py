"""Tests for the LabVIEW Formula Node -> Python transpiler.

Covers the documented-grammar parser, the specific Python transformations
(LabVIEW numeric semantics), and the fail-loud behaviour on unsupported
constructs. The emitted function is exec'd to confirm it is valid Python and
computes the right values.
"""

from __future__ import annotations

import pytest

from lvkit.formula import FormulaTranspileError
from lvkit.formula.cparser import parse
from lvkit.formula.emit import VarSpec, transpile


def _src(script: str, variables: list[VarSpec]) -> str:
    return transpile(script, variables).source


def _run(script: str, variables: list[VarSpec], func_name: str = "f", **inputs):
    """Transpile, exec the emitted function, call it, return its output dict."""
    res = transpile(script, variables, func_name=func_name)
    ns: dict = {}
    exec("import math\nfrom lvkit.runtime import lv as _lv\n" + res.source, ns)
    return ns[func_name](**inputs)


# --- parser ---------------------------------------------------------------


def test_parses_control_flow_and_ops():
    tree = parse(
        "int16 i=0;\n"
        "for(i=0;i<n;i++){ a[i]=b[i]+2**3; }\n"
        "if (x<=0) x=x+1; else x=x-1;\n"
    )
    assert len(tree.stmts) == 3


# --- numeric semantics ----------------------------------------------------


def test_power_emits_python_power_and_computes():
    out = _src("y = 2**16;", [VarSpec("y", "NumFloat64", "out", False)])
    assert "2 ** 16" in out
    assert _run("y = 2**16;", [VarSpec("y", "NumFloat64", "out", False)])["y"] == 65536


def test_int_division_is_real_not_floor():
    # int/int is REAL division in a Formula Node (Python '/' already is).
    variables = [
        VarSpec("a", "NumInt32", "in", False),
        VarSpec("b", "NumInt32", "in", False),
        VarSpec("y", "NumFloat64", "out", False),
    ]
    assert _run("y = a / b;", variables, a=8192, b=32768)["y"] == 0.25


def test_float_to_int_assignment_rounds_ties_to_even():
    variables = [
        VarSpec("a", "NumFloat64", "in", False),
        VarSpec("b", "NumFloat64", "in", False),
        VarSpec("k", "NumInt16", "out", False),
    ]
    out = _src("k = a / b;", variables)
    assert "_lv.i16(" in out  # int target -> width coercion
    # 7/2 = 3.5 -> round-half-to-even -> 4 (not C's truncate-to-3)
    assert _run("k = a / b;", variables, a=7.0, b=2.0)["k"] == 4


def test_fixed_width_integer_wraps():
    # uInt8 stores wrap at 256, matching LabVIEW's fixed-width integer.
    variables = [
        VarSpec("a", "NumInt32", "in", False),
        VarSpec("k", "NumUInt8", "out", False),
    ]
    assert _run("k = a;", variables, a=300)["k"] == 44  # 300 % 256


def test_modulo_is_truncated_remainder():
    variables = [
        VarSpec("y", "NumFloat64", "out", False),
        VarSpec("a", "NumFloat64", "in", False),
        VarSpec("b", "NumFloat64", "in", False),
    ]
    # LV `%` is the truncated remainder (sign of the dividend) for ints and
    # floats alike, so it always routes through the helper, not Python `%`.
    assert "_lv.rem(a, b)" in _src("y = a % b;", variables)
    assert _run("y = a % b;", variables, a=7.5, b=2.0)["y"] == 1.5


def test_abs_is_polymorphic():
    # Python abs handles both int and float — no fabs/abs dispatch needed.
    fout = _run(
        "y = abs(x);",
        [
            VarSpec("y", "NumFloat64", "out", False),
            VarSpec("x", "NumFloat64", "in", False),
        ],
        x=-2.5,
    )
    assert fout["y"] == 2.5
    iout = _run(
        "y = abs(x);",
        [
            VarSpec("y", "NumInt32", "out", False),
            VarSpec("x", "NumInt32", "in", False),
        ],
        x=-7,
    )
    assert iout["y"] == 7


def test_int_function_rounds():
    out = _src(
        "y = int(x);",
        [
            VarSpec("y", "NumFloat64", "out", False),
            VarSpec("x", "NumFloat64", "in", False),
        ],
    )
    assert "round(x)" in out


def test_terminal_redeclaration_becomes_assignment():
    # 'r' is a terminal (already a parameter/local); the script's own
    # `int32 r=0;` must become a plain assignment, not a typed declaration.
    variables = [
        VarSpec("r", "NumInt32", "out", False),
        VarSpec("a", "NumInt32", "in", False),
    ]
    out = _src("int32 r=0;\nr = a;", variables)
    assert "int32" not in out  # no C-style declaration
    assert _run("int32 r=0;\nr = a;", variables, a=5)["r"] == 5


def test_signature_inputs_and_outputs():
    res = transpile(
        "a[0] = x;",
        [
            VarSpec("a", "NumFloat64", "inout", True),
            VarSpec("x", "NumFloat64", "in", False),
            VarSpec("r", "NumInt32", "out", False),
        ],
    )
    assert res.input_names == ["a", "x"]  # in + inout
    assert res.output_names == ["a", "r"]  # inout + out


# --- loops ----------------------------------------------------------------


def test_for_loop_becomes_range_and_runs():
    variables = [
        VarSpec("n", "NumInt32", "in", False),
        VarSpec("data", "NumFloat64", "in", True),
        VarSpec("out", "NumFloat64", "inout", True),
    ]
    script = "int32 i=0;\nfor(i=0;i<n;i++) out[i]=data[i]*2;"
    assert "for i in range(n):" in _src(script, variables)
    r = _run(script, variables, n=3, data=[1.0, 2.0, 3.0], out=[0.0, 0.0, 0.0])
    assert r["out"] == [2.0, 4.0, 6.0]


def test_while_loop_runs():
    r = _run(
        "int32 i=0;\nwhile (i < n) { acc = acc + i; i = i + 1; }",
        [
            VarSpec("n", "NumInt32", "in", False),
            VarSpec("acc", "NumFloat64", "out", False),
        ],
        n=4,
    )
    assert r["acc"] == 6.0  # 0+1+2+3


def test_do_while_runs_body_once():
    # do/while runs the body before testing — so it executes even when the
    # condition is false at entry.
    r = _run(
        "int32 i=0;\ndo { acc = acc + 1; i = i + 1; } while (i < n);",
        [
            VarSpec("n", "NumInt32", "in", False),
            VarSpec("acc", "NumFloat64", "out", False),
        ],
        n=0,
    )
    assert r["acc"] == 1.0


# --- fail loud ------------------------------------------------------------


def test_unknown_function_fails_loud():
    with pytest.raises(FormulaTranspileError):
        _src("y = bogus(x);", [VarSpec("y", "NumFloat64", "out", False)])


def test_unsupported_keyword_fails_loud():
    with pytest.raises(FormulaTranspileError):
        parse("switch(x){ }")


def test_bad_character_fails_loud():
    with pytest.raises(FormulaTranspileError):
        parse("y = @x;")


def test_output_only_array_fails_loud():
    # A pure-output array has no length source — fail loud rather than guess.
    with pytest.raises(FormulaTranspileError):
        _src("out[0] = 1;", [VarSpec("out", "NumFloat64", "out", True)])


# --- emitted code is valid Python -----------------------------------------


def test_emitted_function_is_valid_python_and_self_consistent():
    script = (
        "int32 i=0;\n"
        "float acc=0;\n"
        "for(i=0;i<n;i++){\n"
        "  acc = acc + data[i]*2**1;\n"
        "  if (acc > 32767) acc = acc - 2**16;\n"
        "}\n"
        "gain = int(acc / n) & 65520;\n"
        "out[0] = acc;\n"
    )
    variables = [
        VarSpec("data", "NumFloat64", "in", True),
        VarSpec("n", "NumInt32", "in", False),
        VarSpec("gain", "NumInt16", "out", False),
        VarSpec("out", "NumFloat64", "inout", True),
    ]
    r = _run(script, variables, data=[10.0, 20.0, 30.0], n=3, out=[0.0])
    assert isinstance(r["gain"], int)
    assert r["out"][0] == 120.0  # (10+20+30)*2, none exceed 32767
