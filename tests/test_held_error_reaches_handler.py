"""A parallel branch's error is held until the node that consumes it.

Two branches run in parallel: the error of ``Gen`` goes to an error case
structure (or a Clear Errors) and the error of ``Other`` to a Merge Errors after
it. A branch whose error a handler consumes raises it inside the handler's
``try``, so the Error frame runs; the other branch holds its error until the
merge. Each test builds a tiny block diagram, asserts the source and runs it.
"""

from __future__ import annotations

import importlib

import pytest

from lvkit.graph.models import CaseStructureNode, PrimitiveNode
from lvkit.models import CaseFrame, LVType, LVTypeKind
from tests.test_error_codegen import (
    ERR,
    ERR_OUT,
    INT,
    NO_ERROR,
    RESULT_OUT,
    X_IN,
    call,
    diagram,
    errors_of,
    step,
    term,
    tunnel,
)

INT_ARRAY = LVType(kind=LVTypeKind.ARRAY, element_type=INT, dimensions=1)


def _gen() -> object:
    return call(
        "G",
        "Gen.vi",
        [term("G_x", "input", 0, "x", INT)],
        [
            term("G_r", "output", 1, "r", INT),
            term("G_arr", "output", 2, "arr", INT_ARRAY),
            term("G_eout", "output", 3, "error out", ERR),
        ],
    )


def _merge() -> PrimitiveNode:
    return PrimitiveNode(
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


def _case_diagram(*, passes_error: bool) -> str:
    """The error case on ``Gen``'s error: its No Error frame calls ``Use``, its
    Error frame calls ``Report`` with ``Gen``'s array. The error out of the case
    goes to the Merge Errors, with ``Other``'s error."""
    report = call(
        "R",
        "Report.vi",
        [
            term("R_x", "input", 0, "x", INT),
            term("R_arr", "input", 1, "arr", INT_ARRAY),
        ],
        [term("R_r", "output", 2, "r", INT)],
        parent="CS",
        frame="Error",
    )
    case = CaseStructureNode(
        id="CS",
        vi_path="caller.vi",
        name="Case",
        node_type="caseStruct",
        selector_terminal="sel",
        frames=[
            CaseFrame(selector_value=NO_ERROR, is_no_error=True),
            CaseFrame(selector_value="Error"),
        ],
        terminals=[
            term("sel", "input", 0, "sel", ERR),
            tunnel("t_in", "input", "outer", "t_in_e", ERR),
            tunnel("t_in_e", "output", "inner", "t_in", ERR, frame="Error"),
            tunnel("t_out", "output", "outer", "t_out_e", ERR),
            tunnel("t_out_e", "input", "inner", "t_out", ERR, frame="Error"),
            tunnel("d_out", "output", "outer", "d_n", INT, name="data"),
            tunnel("d_n", "input", "inner", "d_out", INT, NO_ERROR, name="data"),
            tunnel("d_e", "input", "inner", "d_out", INT, "Error", name="data"),
        ],
    )
    wires = [
        ("x", "G_x"),
        ("x", "O_x"),
        ("G_eout", "sel"),
        ("G_eout", "t_in"),
        ("O_eout", "m_in0"),
        ("t_out", "m_in1"),
        ("m_out", "eout"),
        ("G_r", "Q_x"),
        ("Q_r", "d_n"),
        ("G_r", "R_x"),
        ("G_arr", "R_arr"),
        ("R_r", "d_e"),
        ("d_out", "result"),
    ]
    if passes_error:
        wires.append(("t_in_e", "t_out_e"))
    return diagram(
        [
            _gen(),
            step("O", "Other.vi"),
            _merge(),
            step("Q", "Use.vi", parent="CS", frame=NO_ERROR),
            report,
            case,
        ],
        wires,
        inputs=[X_IN],
        outputs=[RESULT_OUT, ERR_OUT],
    )


def _clear_diagram() -> str:
    """``Gen``'s error goes through Clear Errors to ``Use``; ``Other``'s error
    goes to the Merge Errors."""
    clear = call(
        "C",
        "Clear Errors.vi",
        [term("c_ein", "input", 4, "error in", ERR)],
        [term("c_eout", "output", 0, "error out", ERR)],
    )
    return diagram(
        [_gen(), step("O", "Other.vi"), _merge(), clear, step("Q", "Use.vi")],
        [
            ("x", "G_x"),
            ("x", "O_x"),
            ("G_eout", "c_ein"),
            ("c_eout", "Q_ein"),
            ("O_eout", "m_in0"),
            ("c_eout", "m_in1"),
            ("m_out", "eout"),
            ("G_r", "Q_x"),
            ("Q_r", "result"),
        ],
        inputs=[X_IN],
        outputs=[RESULT_OUT, ERR_OUT],
    )


def _fails(name: str, code: int | None) -> str:
    if code is None:
        return f"""
            from types import SimpleNamespace
            def {name}(x=0):
                return SimpleNamespace(r=x, arr=[1, 2], error_out=None)
        """
    return f"""
        from .errors import LabVIEWError
        def {name}(x=0):
            raise LabVIEWError(code={code})
    """


RECORDER = """
    from types import SimpleNamespace
    calls = []
    def {name}(x=0, arr=None):
        calls.append((x, None if arr is None else len(arr)))
        return SimpleNamespace(r=100)
"""


def _run(package, source, gen, other, caller_kwargs=None):
    stubs = {
        "gen": _fails("gen", gen),
        "other": _fails("other", other),
        "use": RECORDER.format(name="use"),
        "report": RECORDER.format(name="report"),
    }
    module = package(source, stubs)
    root = module.__name__.rsplit(".", 1)[0]
    calls = {
        name: importlib.import_module(f"{root}.{name}").calls
        for name in ("use", "report")
    }
    return module, calls


def test_the_error_of_a_branch_an_error_case_consumes_is_raised_in_its_try() -> None:
    source = _case_diagram(passes_error=True)
    assert "    except LabVIEWError as e:\n        report_result" in source
    gen_wait = source.index("gen_result = _f0.result()")
    other_wait = source.index("other_result = _f1.result()")
    assert other_wait < gen_wait
    assert "        raise\n" in source[gen_wait:]


def test_a_failed_branch_runs_the_error_frame_and_the_error_leaves_the_case(
    package,
) -> None:
    module, calls = _run(package, _case_diagram(passes_error=True), 7, None)
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 7
    assert calls["report"] == [(0, 0)]
    assert calls["use"] == []


def test_a_handled_error_runs_the_error_frame_and_nothing_is_raised(package) -> None:
    module, calls = _run(package, _case_diagram(passes_error=False), 7, None)
    assert module.caller(x=1).result == 100
    assert calls["report"] == [(0, 0)]
    assert calls["use"] == []


def test_the_other_branch_error_waits_for_the_merge(package) -> None:
    module, calls = _run(package, _case_diagram(passes_error=True), None, 8)
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 8
    assert calls["use"] == [(1, None)]
    assert calls["report"] == []


def test_both_branches_failing_runs_the_error_frame_and_the_first_error_leaves(
    package,
) -> None:
    module, calls = _run(package, _case_diagram(passes_error=True), 7, 8)
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 7
    assert calls["report"] == [(0, 0)]


def test_a_handled_error_leaves_the_error_of_the_other_branch_for_the_merge(
    package,
) -> None:
    module, calls = _run(package, _case_diagram(passes_error=False), 7, 8)
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 8
    assert calls["report"] == [(0, 0)]


def test_clear_errors_drops_the_error_of_the_branch_it_consumes(package) -> None:
    source = _clear_diagram()
    module, calls = _run(package, source, 7, None)
    assert module.caller(x=1).result == 100
    assert calls["use"] == [(0, None)]
    module, calls = _run(package, source, 7, 8)
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 8


def test_an_error_wired_to_a_clear_errors_and_on_to_a_merge_still_reaches_it(
    package,
) -> None:
    clear = call(
        "C",
        "Clear Errors.vi",
        [term("c_ein", "input", 4, "error in", ERR)],
        [term("c_eout", "output", 0, "error out", ERR)],
    )
    source = diagram(
        [
            _gen(),
            step("O", "Other.vi"),
            step("N", "Mid.vi"),
            _merge(),
            clear,
            step("Q", "Use.vi"),
        ],
        [
            ("x", "G_x"),
            ("x", "O_x"),
            ("x", "N_x"),
            ("G_eout", "c_ein"),
            ("G_eout", "N_ein"),
            ("N_eout", "m_in0"),
            ("O_eout", "m_in1"),
            ("c_eout", "Q_ein"),
            ("m_out", "eout"),
            ("G_r", "Q_x"),
            ("Q_r", "result"),
        ],
        inputs=[X_IN],
        outputs=[RESULT_OUT, ERR_OUT],
    )
    stubs = {
        "gen": _fails("gen", 7),
        "other": _fails("other", None),
        "mid": _fails("mid", None),
        "use": RECORDER.format(name="use"),
    }
    module = package(source, stubs)
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 7
    root = module.__name__.rsplit(".", 1)[0]
    assert importlib.import_module(f"{root}.use").calls == [(0, None)]
