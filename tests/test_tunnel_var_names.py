"""A structure output tunnel's variable is named from its terminal; a name already
taken is numbered ``name_2``, ``name_3`` like every other variable, never with a
node id."""

from __future__ import annotations

from lvkit.codegen.context import CodeGenContext
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import CaseStructureNode
from lvkit.models import TunnelTerminal
from tests.helpers import register_nodes


def _context(*outer_names: str) -> CodeGenContext:
    terminals = []
    for i, name in enumerate(outer_names):
        outer, inner = f"{name}_{i}_outer", f"{name}_{i}_inner"
        terminals.append(
            TunnelTerminal(
                id=outer,
                index=2 * i,
                name=name,
                direction="output",
                boundary="outer",
                paired_id=inner,
            )
        )
        terminals.append(
            TunnelTerminal(
                id=inner,
                index=2 * i + 1,
                direction="input",
                boundary="inner",
                paired_id=outer,
            )
        )
    graph = InMemoryVIGraph()
    node = CaseStructureNode(
        id="vi::900",
        vi_path="test.vi",
        name="Case",
        node_type="caseStruct",
        terminals=terminals,
        selector_terminal=None,
    )
    register_nodes(graph, [node])
    return CodeGenContext(graph=graph)


def test_a_second_tunnel_with_the_same_name_is_numbered_not_given_a_node_id():
    ctx = _context("x", "x", "x")
    names = [ctx.output_tunnel_var_name(f"x_{i}_outer", "vi::900") for i in range(3)]
    assert names == ["x", "x_2", "x_3"]


def test_a_tunnel_keeps_its_name_for_every_later_caller():
    ctx = _context("x", "x")
    first = ctx.output_tunnel_var_name("x_1_outer", "vi::900")
    ctx.output_tunnel_var_name("x_0_outer", "vi::900")
    assert [ctx.output_tunnel_var_name("x_1_outer", "vi::900") for _ in range(3)] == [
        first
    ] * 3


def test_the_names_are_the_same_in_every_generation():
    def run() -> list[str | None]:
        ctx = _context("x", "x", "y")
        return [
            ctx.output_tunnel_var_name(f"{n}_{i}_outer", "vi::900")
            for i, n in enumerate(["x", "x", "y"])
        ]

    assert run() == run() == ["x", "x_2", "y"]
