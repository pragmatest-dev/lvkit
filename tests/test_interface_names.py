"""A VI's parameter and result names are made once, from its terminals, and a
caller uses the same names: two labels that normalise alike are told apart the
same way in the function and in every call of it."""

from __future__ import annotations

from pathlib import Path

from lvkit.codegen.builder import build_module
from lvkit.graph.models import VIContext, VINode, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind
from tests import vi_reference_diagrams as d
from tests.helpers import build_graph

_INT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")


def _fp(id: str, index: int, direction: str, name: str) -> FPTerminal:
    return FPTerminal(
        id=id,
        index=index,
        direction=direction,
        name=name,
        is_public=True,
        is_indicator=direction == "output",
        lv_type=_INT,
    )


def test_a_caller_reads_the_fields_and_passes_the_keywords_the_callee_names(
    tmp_path: Path,
) -> None:
    loaded = d.load(tmp_path, [], [])
    callee_node = loaded.graph._graph.nodes[loaded.callee]["node"]  # noqa: SLF001
    assert isinstance(callee_node, VINode)
    # "A B" and "A-B" are the same Python name; so are the two outputs "R 1"/"R-1".
    callee_node.terminals = [
        _fp("c:in0", 0, "input", "A B"),
        _fp("c:in1", 1, "input", "A-B"),
        _fp("c:out2", 2, "output", "R 1"),
        _fp("c:out3", 3, "output", "R-1"),
    ]

    callee_source = build_module(
        loaded.graph.get_vi_context(loaded.callee),
        loaded.callee,
        graph=loaded.graph,
    )
    assert "def callee(*, a_b: int=0, a_b_2: int=0)" in callee_source
    assert "r_1: int" in callee_source and "r_1_2: int" in callee_source

    call = VINode(
        id="call",
        vi_path="caller.vi",
        name="Callee.vi",
        node_type="iUse",
        callee_key=loaded.callee,
        terminals=[
            _fp("k:in0", 0, "input", "A B"),
            _fp("k:in1", 1, "input", "A-B"),
            _fp("k:out2", 2, "output", "R 1"),
            _fp("k:out3", 3, "output", "R-1"),
        ],
    )
    context = VIContext(
        name="caller.vi",
        inputs=[
            _fp("fp:first", 0, "input", "first"),
            _fp("fp:second", 1, "input", "second"),
        ],
        outputs=[_fp("fp:r1", 0, "output", "r1"), _fp("fp:r2", 1, "output", "r2")],
        data_flow=[
            Wire.from_terminals(from_terminal_id="fp:first", to_terminal_id="k:in0"),
            Wire.from_terminals(from_terminal_id="fp:second", to_terminal_id="k:in1"),
            Wire.from_terminals(from_terminal_id="k:out2", to_terminal_id="fp:r1"),
            Wire.from_terminals(from_terminal_id="k:out3", to_terminal_id="fp:r2"),
        ],
    )
    graph = build_graph(context, "caller.vi", [call], graph=loaded.graph)
    caller_source = build_module(context, "caller.vi", graph=graph)

    # The callee's body swaps its inputs: the first result is the second input.
    namespace: dict = {"__name__": "generated"}
    exec(  # noqa: S102
        "from typing import NamedTuple\n"
        "class Result(NamedTuple):\n    r_1: int\n    r_1_2: int\n"
        "def callee(a_b=0, a_b_2=0):\n    return Result(r_1=a_b_2, r_1_2=a_b)\n",
        namespace,
    )
    # The callee is already in the namespace: drop the import of its module.
    body = "\n".join(
        line for line in caller_source.splitlines() if not line.startswith("from .")
    )
    exec(compile(body, "caller", "exec"), namespace)  # noqa: S102
    result = namespace["caller"](first=1, second=2)
    assert (result.r1, result.r2) == (2, 1)
