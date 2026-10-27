"""A parallel branch hands back only the values its structure sends out.

A value computed inside a case frame and used only there (here: read, then
raised in an error) is not an output of the branch, so the branch does not
return it. A case output tunnel that one frame leaves unwired still reads the
type's default in that frame. Every branch is awaited, so an error raised in a
branch with no outputs reaches the caller.
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from lvkit.codegen.builder import _generate_parallel_tier
from lvkit.codegen.context import CodeGenContext
from lvkit.graph.models import CaseStructureNode, PrimitiveNode
from lvkit.models import CaseFrame, LVType, LVTypeKind, Terminal, TunnelTerminal
from tests.helpers import make_graph_with_terminals, register_nodes


def _lv(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def _case(tunnel: bool) -> tuple[CaseStructureNode, list[PrimitiveNode]]:
    terminals: list = [
        Terminal(id="sel", index=0, direction="input", lv_type=_lv("Boolean"))
    ]
    if tunnel:
        terminals += [
            TunnelTerminal(
                id="outer",
                index=1,
                direction="output",
                boundary="outer",
                tunnel_type="csTun",
                paired_id="inner_true",
                lv_type=_lv("NumInt32"),
            ),
            TunnelTerminal(
                id="inner_true",
                index=2,
                direction="input",
                boundary="inner",
                tunnel_type="csTun",
                paired_id="outer",
                frame="True",
            ),
        ]
    case = CaseStructureNode(
        id="case1",
        vi_path="test.vi",
        name="Case",
        node_type="caseStruct",
        terminals=terminals,
        selector_terminal="sel",
        frames=[
            CaseFrame(selector_value="True", boolean_value=True),
            CaseFrame(selector_value="False", boolean_value=False),
        ],
    )
    op = PrimitiveNode(
        id="op_true",
        vi_path="test.vi",
        name="Op",
        terminals=[Terminal(id="op_out", index=0, direction="output")],
        parent="case1",
        frame="True",
    )
    return case, [op]


def _source(tunnel: bool, frame_code: str) -> str:
    case, ops = _case(tunnel)
    graph = make_graph_with_terminals("sel")
    register_nodes(graph, [case, *ops])
    ctx = CodeGenContext(graph=graph)
    ctx.bind("sel", "flag")
    if tunnel:
        ctx.bind("inner_true", "inner_value")

    def body(operations, inner_ctx):
        if not operations:
            return []
        inner_ctx.bind("op_out", "inner_value")
        return ast.parse(textwrap.dedent(frame_code)).body

    ctx._body_generator = body
    stmts = _generate_parallel_tier([case], ctx)
    module = ast.fix_missing_locations(ast.Module(body=stmts, type_ignores=[]))
    return ast.unparse(module)


def _run(source: str, flag: bool) -> dict:
    scope: dict = {"flag": flag}
    exec(f"import concurrent.futures\n{source}", scope)
    return scope


_RAISING_FRAME = """
inner_value = 5
if inner_value != 0:
    raise ValueError(inner_value)
"""


def test_a_value_used_only_inside_a_frame_is_not_returned() -> None:
    source = _source(tunnel=False, frame_code=_RAISING_FRAME)
    assert "return" not in source
    assert "inner_value =" in source  # only the frame's own assignment
    assert "_f0 = _executor.submit(_branch_0)" in source
    assert "_f0.result()" in source


def test_the_frame_that_raises_still_raises_and_the_other_frame_runs() -> None:
    source = _source(tunnel=False, frame_code=_RAISING_FRAME)
    with pytest.raises(ValueError):
        _run(source, flag=True)
    _run(source, flag=False)


def test_an_unwired_frame_of_an_output_tunnel_reads_the_type_default() -> None:
    source = _source(tunnel=True, frame_code="inner_value = 7")
    assert "case_output = 0" in source
    assert _run(source, flag=True)["case_output"] == 7
    assert _run(source, flag=False)["case_output"] == 0
