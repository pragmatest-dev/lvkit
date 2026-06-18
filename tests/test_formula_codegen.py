"""Tests for Formula Node codegen + pipeline artifact emission.

Self-contained (no .vi fixture): builds a FormulaOperation directly, runs the
node generator, and checks that it emits the loader call, binds outputs, and
registers a C artifact that the pipeline helper writes + compiles.
"""

from __future__ import annotations

import ast
import shutil

import pytest

from lvkit.codegen.context import CodeGenContext, FormulaArtifact
from lvkit.codegen.nodes import formula
from lvkit.models import FormulaOperation, LVType, Terminal

CC = shutil.which("cc") or shutil.which("gcc")


def _dbl() -> LVType:
    return LVType(kind="primitive", underlying_type="NumFloat64")


def _op() -> FormulaOperation:
    return FormulaOperation(
        id="my_vi.vi::42",
        name="Formula Node",
        labels=["FormulaNode"],
        node_type="fBox",
        script="y = a + 2**3;",
        terminals=[
            Terminal(id="t_a", index=0, direction="input", name="a", lv_type=_dbl()),
            Terminal(id="t_y", index=1, direction="output", name="y", lv_type=_dbl()),
        ],
    )


def test_generate_emits_loader_and_binds_output():
    op = _op()
    ctx = CodeGenContext(vi_name="my_vi.vi")
    frag = formula.generate(op, ctx)

    # An artifact was registered for the pipeline to write + compile.
    assert len(ctx.formula_artifacts) == 1
    art = ctx.formula_artifacts[0]
    assert art.basename == "my_vi_vi_formula_42"
    assert "void formula_42(" in art.c_source

    src = "\n".join(ast.unparse(s) for s in frag.statements)
    assert "_lvkit_formula.load(" in src
    assert "_formula_42(" in src
    # output terminal bound to a fresh variable read out of the result dict
    assert frag.bindings.get("t_y")
    assert f"{frag.bindings['t_y']} = " in src
    assert "from lvkit.runtime import formula as _lvkit_formula" in frag.imports


@pytest.mark.skipif(CC is None, reason="no C compiler available")
def test_pipeline_emits_c_and_compiles_so(tmp_path):
    from lvkit.formula.compile import platform_tag
    from lvkit.pipeline import _emit_formula_artifacts

    op = _op()
    ctx = CodeGenContext(vi_name="my_vi.vi")
    formula.generate(op, ctx)

    _emit_formula_artifacts(ctx.formula_artifacts, tmp_path)

    base = "my_vi_vi_formula_42"
    assert (tmp_path / f"{base}.c").exists()
    assert (tmp_path / f"{base}.{platform_tag()}.so").exists()


def test_unknown_function_in_script_fails_loud():
    op = _op()
    op.script = "y = mystery(a);"
    ctx = CodeGenContext(vi_name="my_vi.vi")
    from lvkit.formula import FormulaTranspileError

    with pytest.raises(FormulaTranspileError):
        formula.generate(op, ctx)


def test_artifact_dataclass_roundtrip():
    art = FormulaArtifact("base", "int x;")
    assert art.basename == "base"
    assert art.c_source == "int x;"
