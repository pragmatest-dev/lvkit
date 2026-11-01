"""Error case structures with several error frames (generate only).

An error frame selected by an error code is an ``if`` on ``e.code`` in the
handler of the producers; the frame that covers the remaining errors is the
``else``. A frame raises the error again only when it sends it out of the case
unchanged. Each test builds a tiny block diagram, asserts the generated source
and runs it.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph.models import CaseStructureNode, VIContext, Wire
from lvkit.models import CaseFrame, SelectorRange
from tests.helpers import build_graph
from tests.test_error_codegen import (  # noqa: F401
    ERR,
    ERR_IN,
    ERR_OUT,
    INT,
    NO_ERROR,
    RESULT_OUT,
    X_IN,
    errors_of,
    step,
    stub,
    term,
    tunnel,
)

PLAIN_ERROR = (SelectorRange(start=1, end=1),)


@dataclass
class ErrFrame:
    """An error frame: its name, the error codes it lists, whether it is the
    Default frame, the subVI it calls (None: nothing), and whether it wires the
    error it caught to the error output tunnel."""

    name: str
    ranges: tuple[SelectorRange, ...] = ()
    default: bool = False
    calls: str | None = None
    keeps: bool = False


def code(start: int, end: int | None = None) -> tuple[SelectorRange, ...]:
    return (SelectorRange(start=start, end=start if end is None else end),)


def _source(
    frames: list[ErrFrame], *, wired_out: bool = True, from_caller: bool = False
) -> str:
    """``Open`` raises -> a case on its error. The No Error frame calls ``Use``;
    each error frame calls its subVI and sends the result out of the case."""
    nodes: list = [
        step("P", "Open.vi"),
        step("Q", "Use.vi", parent="CS", frame=NO_ERROR),
    ]
    terminals = [
        term("sel", "input", 0, "sel", ERR),
        tunnel("t_in", "input", "outer", "t_in_0", ERR),
        tunnel("t_out", "output", "outer", "t_out_0", ERR),
        tunnel("d_out", "output", "outer", "d_n", INT, name="data"),
        tunnel("d_n", "input", "inner", "d_out", INT, frame=NO_ERROR, name="data"),
    ]
    wires = [
        ("x", "P_x"),
        ("ein" if from_caller else "P_eout", "sel"),
        ("ein" if from_caller else "P_eout", "t_in"),
        ("P_r", "Q_x"),
        ("Q_r", "d_n"),
        ("d_out", "result"),
    ]
    if wired_out:
        wires.append(("t_out", "eout"))
    case_frames = [CaseFrame(selector_value=NO_ERROR, is_no_error=True)]
    for i, f in enumerate(frames):
        terminals += [
            tunnel(f"t_in_{i}", "output", "inner", "t_in", ERR, frame=f.name),
            tunnel(f"t_out_{i}", "input", "inner", "t_out", ERR, frame=f.name),
            tunnel(f"d_{i}", "input", "inner", "d_out", INT, f.name, name="data"),
        ]
        if f.keeps:
            wires.append((f"t_in_{i}", f"t_out_{i}"))
        if f.calls:
            nodes.append(step(f"R{i}", f.calls, parent="CS", frame=f.name))
            wires += [("x", f"R{i}_x"), (f"R{i}_r", f"d_{i}")]
        case_frames.append(
            CaseFrame(
                selector_value="Default" if f.default else f.name,
                is_default=f.default,
                selector_ranges=list(f.ranges),
            )
        )
        for t in terminals:
            if getattr(t, "frame", None) == f.name and f.default:
                t.frame = "Default"
    nodes.append(
        CaseStructureNode(
            id="CS",
            vi_path="caller.vi",
            name="Case",
            node_type="caseStruct",
            selector_terminal="sel",
            frames=case_frames,
            terminals=terminals,
        )
    )
    inputs = [X_IN, *([ERR_IN] if from_caller else [])]
    vi_context = VIContext(
        name="Caller",
        inputs=inputs,
        outputs=[RESULT_OUT, ERR_OUT],
        data_flow=[
            Wire.from_terminals(from_terminal_id=a, to_terminal_id=b) for a, b in wires
        ],
    )
    for n in nodes:
        if getattr(n, "frame", None) in {f.name for f in frames if f.default}:
            n.frame = "Default"
    graph = build_graph(vi_context, "caller.vi", nodes)
    return build_module(vi_context, "caller.vi", graph=graph)


def _stubs(open_code: int | None, **extra: str) -> dict[str, str]:
    raising = (
        f"""
        from .errors import LabVIEWError
        def open_(x=0):
            raise LabVIEWError(code={open_code})
        """
        if open_code is not None
        else stub("open_", 0)
    )
    return {"open_": raising, "use": stub("use", 100), **extra}


CLEAR_THEN_RETRY = [
    ErrFrame("Error -1012", code(-1012)),
    ErrFrame("Default", default=True, calls="Retry.vi"),
]


def test_code_frame_that_clears_the_error_and_a_default_frame_that_retries() -> None:
    assert _source(CLEAR_THEN_RETRY) == textwrap.dedent(
        """\
        from __future__ import annotations
        from typing import Any, NamedTuple
        from .errors import LabVIEWError
        from .open_ import open_
        from .retry import retry
        from .use import use

        class CallerResult(NamedTuple):
            result: int

        def caller(x: int=0) -> CallerResult:
            data = 0
            try:
                open__result = open_(x=x)
            except LabVIEWError as e:
                if e.code == -1012:
                    pass
                else:
                    retry_result = retry(x=x)
                    data = retry_result.r
            else:
                use_result = use(x=open__result.r)
                data = use_result.r
            return CallerResult(result=data)"""
    )


def test_the_cleared_code_returns_the_frame_outputs_and_other_codes_retry(
    package,
) -> None:
    source = _source(CLEAR_THEN_RETRY)
    retry = stub("retry", 200)
    module = package(source, _stubs(-1012, retry=retry))
    assert module.caller(x=1).result == 0
    module = package(source, _stubs(5, retry=retry))
    assert module.caller(x=1).result == 201
    module = package(source, _stubs(None, retry=retry))
    assert module.caller(x=1).result == 101


def test_a_retry_that_fails_raises_its_own_error(package) -> None:
    failing = """
        from .errors import LabVIEWError
        def retry(x=0):
            raise LabVIEWError(code=77)
    """
    module = package(_source(CLEAR_THEN_RETRY), _stubs(5, retry=failing))
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 77


def test_an_error_frame_that_passes_the_error_out_raises_it_again(package) -> None:
    frames = [ErrFrame("Error", PLAIN_ERROR, calls="Report.vi", keeps=True)]
    source = _source(frames)
    handler = (
        "    except LabVIEWError as e:\n"
        "        report_result = report(x=x)\n"
        "        data = report_result.r\n"
        "        raise\n"
        "    else:\n"
    )
    assert handler in source
    module = package(source, _stubs(4, report=stub("report", 300)))
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == 4


def test_an_error_passed_to_an_unwired_error_output_is_not_raised(package) -> None:
    frames = [ErrFrame("Error", PLAIN_ERROR, calls="Report.vi", keeps=True)]
    source = _source(frames, wired_out=False)
    assert "raise" not in source.replace("except", "")
    module = package(source, _stubs(4, report=stub("report", 300)))
    assert module.caller(x=1).result == 301


def test_an_error_frame_that_does_not_pass_the_error_out_does_not_raise(
    package,
) -> None:
    frames = [ErrFrame("Error", PLAIN_ERROR, calls="Report.vi")]
    source = _source(frames)
    assert "raise" not in source.replace("except", "")
    module = package(source, _stubs(4, report=stub("report", 300)))
    assert module.caller(x=1).result == 301


def test_a_range_frame_is_a_comparison_and_uncovered_errors_are_raised(
    package,
) -> None:
    frames = [ErrFrame("Error -1100..-1000", code(-1100, -1000), calls="Fix.vi")]
    source = _source(frames)
    assert "if -1100 <= e.code <= -1000:" in source
    assert source.rstrip().endswith("return CallerResult(result=data)")
    module = package(source, _stubs(-1050, fix=stub("fix", 500)))
    assert module.caller(x=1).result == 501
    module = package(source, _stubs(-1, fix=stub("fix", 500)))
    with pytest.raises(errors_of(module)) as raised:
        module.caller(x=1)
    assert raised.value.code == -1


def test_several_codes_and_ranges_of_a_frame_are_joined_with_or() -> None:
    frame = ErrFrame(
        "Error 5, 7..9",
        (SelectorRange(start=5, end=5), SelectorRange(start=7, end=9)),
        calls="Fix.vi",
    )
    assert "if e.code == 5 or 7 <= e.code <= 9:" in _source([frame])


def test_a_whole_domain_frame_covers_the_remaining_errors(package) -> None:
    whole = (SelectorRange(start=0, end=0, open_start=True, open_end=True),)
    frames = [
        ErrFrame("Error -1012", code(-1012)),
        ErrFrame("Error ..", whole, calls="Report.vi"),
    ]
    source = _source(frames)
    assert "if e.code == -1012:" in source
    module = package(source, _stubs(9, report=stub("report", 300)))
    assert module.caller(x=1).result == 301


def test_an_error_case_on_an_error_from_the_caller_runs_only_no_error() -> None:
    source = _source(CLEAR_THEN_RETRY, from_caller=True)
    assert "except" not in source
    assert "retry" not in source
    assert "use_result = use(" in source
