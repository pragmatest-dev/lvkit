"""Tests for Formula Node codegen.

Self-contained (no .vi fixture): builds a FormulaOperation directly, runs the
node generator, and checks that it injects a module-level Python helper
function, emits a call to it, and binds the outputs — no C artifact, no FFI.
"""

from __future__ import annotations

import ast

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes import formula
from lvkit.models import FormulaOperation, LVType, LVTypeKind, Terminal


def _dbl() -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")


def _i16() -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt16")


def _op(
    script: str = "y = a + 2**3;",
    y_type: LVType | None = None,
) -> FormulaOperation:
    return FormulaOperation(
        id="my_vi.vi::42",
        name="Formula Node",
        kind="formula",
        node_type="fBox",
        script=script,
        terminals=[
            Terminal(id="t_a", index=0, direction="input", name="a", lv_type=_dbl()),
            Terminal(
                id="t_y",
                index=1,
                direction="output",
                name="y",
                lv_type=y_type or _dbl(),
            ),
        ],
    )


def test_generate_injects_helper_and_binds_output():
    op = _op()
    ctx = CodeGenContext(vi_name="my_vi.vi")
    frag = formula.generate(op, ctx)

    # A module-level helper function was injected (no C artifact).
    assert len(ctx.formula_helpers) == 1
    helper = ctx.formula_helpers[0]
    assert isinstance(helper, ast.FunctionDef)
    assert helper.name == "_formula_42"

    src = "\n".join(ast.unparse(s) for s in frag.statements)
    assert "_formula_42(a=" in src  # call with resolved input
    # output terminal bound to a fresh variable read out of the result dict
    assert frag.bindings.get("t_y")
    assert f"{frag.bindings['t_y']} = " in src
    # pure-float script needs no runtime import
    assert not any("runtime import" in i for i in frag.imports)


def test_injected_helper_is_valid_runnable_python():
    op = _op()
    ctx = CodeGenContext(vi_name="my_vi.vi")
    formula.generate(op, ctx)
    helper = ctx.formula_helpers[0]

    mod = ast.Module(body=[helper], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns: dict = {}
    exec(ast.unparse(mod), ns)
    assert ns["_formula_42"](a=5.0)["y"] == 13.0


def test_int_output_pulls_in_lv_runtime_import():
    # An integer-typed output coerces width via _lv, so the import is needed.
    op = _op(script="y = a;", y_type=_i16())
    ctx = CodeGenContext(vi_name="my_vi.vi")
    frag = formula.generate(op, ctx)
    assert "from lvkit.runtime import lv as _lv" in frag.imports


def test_formula_does_not_mutate_callers_array():
    """BEHAVIORAL guard (not a string match): a Formula Node writes its in/out
    array in place, so the caller's array must NOT be mutated — LabVIEW value-
    copies arrays at a wire branch. Built + executed end to end, so it survives
    ANY future copy mechanism (formula-call copy OR value-copy-at-branch): if a
    branching change re-introduces aliasing, the caller's list gets mutated and
    this fails. This is the exact failure mode that corrupted Himmelt's VI."""
    arr = LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=_dbl())
    op = FormulaOperation(
        id="vi::9",
        name="Formula Node",
        kind="formula",
        node_type="fBox",
        script="int32 i=0;\nfor (i=0; i<n; i++) buf[i] = buf[i] + 1;",
        terminals=[
            Terminal(id="t_in", index=0, direction="input", name="buf", lv_type=arr),
            Terminal(id="t_out", index=1, direction="output", name="buf", lv_type=arr),
            Terminal(
                id="t_n",
                index=2,
                direction="input",
                name="n",
                lv_type=LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32"),
            ),
        ],
    )
    from tests.helpers import make_ctx

    ctx = make_ctx("t_in", "t_out", "t_n")
    ctx.bind("t_in", "data")  # the input array wire...
    ctx.bind("t_n", "count")
    frag = formula.generate(op, ctx)

    mod = ast.Module(body=[ctx.formula_helpers[0], *frag.statements], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns: dict = {"data": [1.0, 2.0, 3.0], "count": 3}
    exec("import math\nfrom lvkit.runtime import lv as _lv\n" + ast.unparse(mod), ns)

    assert ns["data"] == [1.0, 2.0, 3.0], "caller's array was mutated (aliased!)"
    out_var = frag.bindings["t_out"]
    assert ns[out_var] == [2.0, 3.0, 4.0]  # the node's own (copied) buffer


def test_unknown_function_in_script_fails_loud():
    op = _op(script="y = mystery(a);")
    ctx = CodeGenContext(vi_name="my_vi.vi")
    from lvkit.formula import FormulaTranspileError

    with pytest.raises(FormulaTranspileError):
        formula.generate(op, ctx)
