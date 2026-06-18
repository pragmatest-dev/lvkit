"""Compile-and-call test for the Formula Node runtime.

Transpiles a small script, compiles it via the runtime loader (no prebuilt
.so present, so it falls back to compiling the .c), and verifies marshaling
of scalar in/out and array in/out across the FFI boundary.
"""

from __future__ import annotations

import shutil

import pytest

from lvkit.formula.emit import VarSpec, transpile
from lvkit.runtime import formula as rt

CC = shutil.which("cc") or shutil.which("gcc")
pytestmark = pytest.mark.skipif(CC is None, reason="no C compiler available")


def test_scalar_and_array_marshaling(tmp_path):
    script = (
        "int16 i=0;\n"
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
    (tmp_path / "ft.c").write_text(res.c_source)
    params = [(p.name, p.ctype, p.role, p.var) for p in res.params]

    fn = rt.load(tmp_path, "ft", "formula_t", params)
    result = fn(a=5.0, n=3, data=[1.0, 2.0, 3.0], out=[0.0, 0.0, 0.0])

    assert result["y"] == 13.0                 # 5 + 2**3
    assert result["out"] == [2.0, 4.0, 6.0]    # data*2, written in place


def test_int_assignment_rounds_at_runtime(tmp_path):
    # 7/2 = 3.5 -> LabVIEW rounds to nearest even -> 4 (C would truncate to 3)
    script = "k = a / b;\n"
    variables = [
        VarSpec("a", "NumFloat64", "in", False),
        VarSpec("b", "NumFloat64", "in", False),
        VarSpec("k", "NumInt32", "out", False),
    ]
    res = transpile(script, variables, func_name="formula_r")
    (tmp_path / "fr.c").write_text(res.c_source)
    params = [(p.name, p.ctype, p.role, p.var) for p in res.params]

    fn = rt.load(tmp_path, "fr", "formula_r", params)
    assert fn(a=7.0, b=2.0)["k"] == 4          # round-to-nearest, not trunc(3)
