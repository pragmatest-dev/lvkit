"""Emitted Formula Node functions execute (scalar + array in/out).

The Formula Node backend emits pure Python (no C compiler / FFI) that imports
nothing from lvkit.
"""

from __future__ import annotations

from lvkit.formula.emit import VarSpec, transpile

# --- emitted function executes (scalar + array marshaling) -----------------


def test_scalar_and_array_round_trip():
    script = "int32 i=0;\ny = a + 2**3;\nfor (i=0; i<n; i++) out[i] = data[i]*2;\n"
    variables = [
        VarSpec("a", "NumFloat64", "in", False),
        VarSpec("n", "NumInt32", "in", False),
        VarSpec("y", "NumFloat64", "out", False),
        VarSpec("data", "NumFloat64", "in", True),
        VarSpec("out", "NumFloat64", "inout", True),
    ]
    res = transpile(script, variables, func_name="formula_t")
    ns: dict = {}
    exec("\n".join(sorted(res.imports)) + "\n" + res.source, ns)
    assert "lvkit" not in res.source
    result = ns["formula_t"](a=5.0, n=3, data=[1.0, 2.0, 3.0], out=[0.0, 0.0, 0.0])

    assert result["y"] == 13.0  # 5 + 2**3
    assert result["out"] == [2.0, 4.0, 6.0]  # data*2, written in place


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
    exec("\n".join(sorted(res.imports)) + "\n" + res.source, ns)
    assert "lvkit" not in res.source
    assert ns["formula_e"](n=0, data=[], out=[])["out"] == []
