"""Tests for the LabVIEW Formula Node -> C transpiler.

Covers the documented-grammar parser, the specific C transformations, and the
fail-loud behaviour on unsupported constructs. Where a C compiler is present,
the emitted C is compiled to verify it is well-formed.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from lvkit.formula import FormulaTranspileError
from lvkit.formula.cparser import parse
from lvkit.formula.emit import VarSpec, transpile

CC = shutil.which("cc") or shutil.which("gcc")


def _c(script: str, variables: list[VarSpec]) -> str:
    return transpile(script, variables).c_source


def _body(script: str, variables: list[VarSpec]) -> str:
    """Just the generated function (prelude stripped), so assertions don't
    match helper definitions like the 'fmod' inside lv_mod."""
    out = transpile(script, variables).c_source
    return "void " + out.split("void ", 1)[1]


# --- parser ---------------------------------------------------------------


def test_parses_control_flow_and_ops():
    tree = parse(
        "int16 i=0;\n"
        "for(i=0;i<n;i++){ a[i]=b[i]+2**3; }\n"
        "if (x<=0) x=x+1; else x=x-1;\n"
    )
    assert len(tree.stmts) == 3


def test_power_is_right_associative_below_unary():
    # -2**2 must parse as -(2**2); round-trips through emit as lv_pow.
    out = _c("y = -2**2;", [VarSpec("y", "NumFloat64", "out", False)])
    assert "(-lv_pow(2, 2))" in out


# --- transformations ------------------------------------------------------


def test_power_maps_to_lv_pow():
    out = _c("y = 2**16;", [VarSpec("y", "NumFloat64", "out", False)])
    assert "lv_pow(2, 16)" in out
    assert "2**16" not in out


def test_int_function_maps_to_lv_int():
    out = _c("y = int(x);", [
        VarSpec("y", "NumFloat64", "out", False),
        VarSpec("x", "NumFloat64", "in", False),
    ])
    assert "lv_int(x)" in out


def test_modulo_float_becomes_fmod_int_stays_percent():
    fout = _body("y = a % b;", [
        VarSpec("y", "NumFloat64", "out", False),
        VarSpec("a", "NumFloat64", "in", False),
        VarSpec("b", "NumFloat64", "in", False),
    ])
    assert "fmod(a, b)" in fout
    iout = _body("y = a % b;", [
        VarSpec("y", "NumInt32", "out", False),
        VarSpec("a", "NumInt32", "in", False),
        VarSpec("b", "NumInt32", "in", False),
    ])
    assert "(a % b)" in iout
    assert "fmod" not in iout


def test_float_to_int_assignment_rounds():
    # int target, float RHS -> wrapped in lv_round (LabVIEW rounds; C truncates)
    out = _c("k = a / b;", [
        VarSpec("k", "NumInt16", "out", False),
        VarSpec("a", "NumFloat64", "in", False),
        VarSpec("b", "NumFloat64", "in", False),
    ])
    assert "lv_round(" in out


def test_int_to_int_assignment_not_rounded():
    out = _body("k = a + b;", [
        VarSpec("k", "NumInt32", "out", False),
        VarSpec("a", "NumInt32", "in", False),
        VarSpec("b", "NumInt32", "in", False),
    ])
    assert "lv_round" not in out
    assert "k = (a + b);" in out


def test_terminal_redeclaration_dropped():
    # 'r' is a terminal (already a function-scope local); the script's own
    # `int32 r=0;` must become a plain assignment, not a second declaration.
    out = _body("int32 r=0;\nr = a;", [
        VarSpec("r", "NumInt32", "out", False),
        VarSpec("a", "NumInt32", "in", False),
    ])
    # 'r' is declared exactly once (the wrapper prologue), not re-declared
    # by the script's own `int32 r=0;`, which becomes a plain assignment.
    assert out.count("int32_t r") == 1
    assert "r = 0;" in out              # initializer kept as assignment


def test_type_keyword_maps_to_stdint():
    out = _c("int16 tmp = 3;\ny = tmp;", [
        VarSpec("y", "NumFloat64", "out", False),
    ])
    assert "int16_t tmp = 3;" in out


def test_signature_roles():
    res = transpile("a[0] = x;", [
        VarSpec("a", "NumFloat64", "inout", True),
        VarSpec("x", "NumFloat64", "in", False),
        VarSpec("r", "NumInt32", "out", False),
    ])
    roles = {p.var: p.role for p in res.params}
    assert roles["a"] == "array_inout"
    assert roles["x"] == "scalar_in"
    assert roles["r"] == "scalar_out"


# --- loops (while / do-while) ---------------------------------------------
# These are supported by the parser and emitter (switch/case/break are not);
# lock that behaviour so a future change can't silently drop them.


def test_while_loop_transpiles():
    out = _body(
        "int32 i=0;\nwhile (i < n) { acc = acc + i; i = i + 1; }",
        [
            VarSpec("n", "NumInt32", "in", False),
            VarSpec("acc", "NumFloat64", "out", False),
        ],
    )
    assert "while ((i < n))" in out
    assert "acc = (acc + i);" in out


def test_do_while_transpiles():
    out = _body(
        "int32 i=0;\ndo { acc = acc + i; i = i + 1; } while (i < n);",
        [
            VarSpec("n", "NumInt32", "in", False),
            VarSpec("acc", "NumFloat64", "out", False),
        ],
    )
    assert "do" in out
    assert "while ((i < n));" in out


@pytest.mark.skipif(CC is None, reason="no C compiler available")
def test_while_and_do_while_c_compiles(tmp_path):
    assert CC is not None
    script = (
        "int32 i=0;\n"
        "while (i < n) { acc = acc + data[i]; i = i + 1; }\n"
        "i = 0;\n"
        "do { acc = acc - 1; i = i + 1; } while (i < n);\n"
    )
    variables = [
        VarSpec("data", "NumFloat64", "in", True),
        VarSpec("n", "NumInt32", "in", False),
        VarSpec("acc", "NumFloat64", "out", False),
    ]
    res = transpile(script, variables)
    src = tmp_path / "loops.c"
    src.write_text(res.c_source)
    obj = tmp_path / "loops.o"
    r = subprocess.run(
        [CC, "-c", "-O2", "-Wall", "-Werror", str(src), "-o", str(obj)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr


# --- fail loud ------------------------------------------------------------


def test_unknown_function_fails_loud():
    with pytest.raises(FormulaTranspileError):
        _c("y = bogus(x);", [VarSpec("y", "NumFloat64", "out", False)])


def test_unsupported_keyword_fails_loud():
    with pytest.raises(FormulaTranspileError):
        parse("switch(x){ }")


def test_bad_character_fails_loud():
    with pytest.raises(FormulaTranspileError):
        parse("y = @x;")


# --- emitted C compiles ---------------------------------------------------


@pytest.mark.skipif(CC is None, reason="no C compiler available")
def test_emitted_c_compiles(tmp_path):
    assert CC is not None
    script = (
        "int16 i=0;\n"
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
    res = transpile(script, variables)
    src = tmp_path / "f.c"
    src.write_text(res.c_source)
    r = subprocess.run(
        [CC, "-c", "-O2", "-Wall", "-Werror", str(src), "-o", str(tmp_path / "f.o")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
