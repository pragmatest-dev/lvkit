"""A parallel branch that fails still outputs the default of each output's type.

Under the held error model the nodes after the merge read the outputs of a failed
branch, so they hold values of their types instead of None. Each test builds a
tiny block diagram, asserts the generated source and runs it.
"""

from __future__ import annotations

import pytest

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind
from tests.test_error_codegen import (
    ERR,
    ERR_OUT,
    INT,
    RESULT_OUT,
    STR,
    X_IN,
    call,
    diagram,
    errors_of,
    stub,
    term,
)

INT_ARRAY = LVType(kind=LVTypeKind.ARRAY, element_type=INT, dimensions=1)


def _gen() -> object:
    return call(
        "G",
        "Gen.vi",
        [
            term("G_x", "input", 0, "x", INT),
            term("G_ein", "input", 1, "error in", ERR),
        ],
        [
            term("G_r", "output", 2, "r", INT),
            term("G_arr", "output", 3, "arr", INT_ARRAY),
            term("G_s", "output", 4, "s", STR),
            term("G_eout", "output", 5, "error out", ERR),
        ],
    )


def _report() -> object:
    return call(
        "R",
        "Report.vi",
        [
            term("R_x", "input", 0, "x", INT),
            term("R_arr", "input", 1, "arr", INT_ARRAY),
            term("R_s", "input", 2, "s", STR),
        ],
        [term("R_r", "output", 3, "r", INT)],
    )


def _source() -> str:
    """``Gen`` and ``Other`` run in parallel and their errors meet at a Merge
    Errors. ``Report`` reads the number, the array and the string ``Gen``
    outputs."""
    from tests.test_error_codegen import step

    merge = PrimitiveNode(
        id="M",
        vi_path="caller.vi",
        name="Merge Errors",
        node_type="prim",
        prim_id=2147,
        terminals=[
            term("m_in0", "input", 0, "error in 0", ERR),
            term("m_in1", "input", 1, "error in 1", ERR),
            term("m_out", "output", 2, "error out", ERR),
        ],
    )
    return diagram(
        [_gen(), step("O", "Other.vi"), merge, _report()],
        [
            ("x", "G_x"),
            ("x", "O_x"),
            ("G_eout", "m_in0"),
            ("O_eout", "m_in1"),
            ("m_out", "eout"),
            ("G_r", "R_x"),
            ("G_arr", "R_arr"),
            ("G_s", "R_s"),
            ("R_r", "result"),
        ],
        inputs=[X_IN],
        outputs=[RESULT_OUT, ERR_OUT],
    )


GEN_FAILS = """
    from .errors import LabVIEWError
    def gen(x=0):
        raise LabVIEWError(code=7)
"""
REPORT = """
    from types import SimpleNamespace
    calls = []
    def report(x=0, arr=None, s=None):
        calls.append((x, len(arr), s))
        return SimpleNamespace(r=len(arr))
"""


def test_the_outputs_of_a_failed_branch_are_the_defaults_of_their_types(
    package,
) -> None:
    source = _source()
    assert "gen_result = SimpleNamespace(r=0, arr=[], s='')" in source
    module = package(
        source,
        {"gen": GEN_FAILS, "other": stub("other"), "report": REPORT},
    )
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 7
    report = __import__(module.__name__.rsplit(".", 1)[0] + ".report", fromlist=["x"])
    assert report.calls == [(0, 0, "")]


@pytest.mark.parametrize(
    ("lv_type", "default"), [(INT_ARRAY, "[]"), (INT, "0"), (STR, "''")]
)
def test_an_output_of_a_failed_branch_is_the_default_of_its_type(
    package, lv_type, default
) -> None:
    from tests.test_error_codegen import step

    gen = call(
        "G",
        "Gen.vi",
        [term("G_x", "input", 0, "x", INT)],
        [term("G_v", "output", 1, "v", lv_type), term("G_eout", "output", 2, "e", ERR)],
    )
    merge = PrimitiveNode(
        id="M",
        vi_path="caller.vi",
        name="Merge Errors",
        node_type="prim",
        prim_id=2147,
        terminals=[
            term("m_in0", "input", 0, "error in 0", ERR),
            term("m_in1", "input", 1, "error in 1", ERR),
            term("m_out", "output", 2, "error out", ERR),
        ],
    )
    report = call(
        "R",
        "Report.vi",
        [term("R_v", "input", 0, "v", lv_type)],
        [term("R_r", "output", 1, "r", INT)],
    )
    source = diagram(
        [gen, step("O", "Other.vi"), merge, report],
        [
            ("x", "G_x"),
            ("x", "O_x"),
            ("G_eout", "m_in0"),
            ("O_eout", "m_in1"),
            ("m_out", "eout"),
            ("G_v", "R_v"),
            ("R_r", "result"),
        ],
        inputs=[X_IN],
        outputs=[RESULT_OUT, ERR_OUT],
    )
    assert f"        gen_result = SimpleNamespace(v={default})\n" in source
    module = package(
        source,
        {
            "gen": GEN_FAILS,
            "other": stub("other"),
            "report": """
                from types import SimpleNamespace
                calls = []
                def report(v=None):
                    calls.append(v)
                    return SimpleNamespace(r=0)
            """,
        },
    )
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 7
    package_name = module.__name__.rsplit(".", 1)[0]
    reported = __import__(package_name + ".report", fromlist=["calls"]).calls
    assert reported == [eval(default)]
