"""A wire that reaches a VI's own control is not a dependency of the nodes that
read the control, so it makes no cycle among them."""

from __future__ import annotations

from lvkit.codegen.builder import topological_sort_tiered
from lvkit.codegen.context import CodeGenContext
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, WireEnd
from lvkit.models import Terminal


def _node(node_id: str, vi: str, terminals: list[Terminal]) -> PrimitiveNode:
    return PrimitiveNode(id=node_id, vi_path=vi, name=node_id, terminals=terminals)


def test_wire_into_a_control_does_not_make_its_readers_circular() -> None:
    vi = "Cyc.vi"
    graph = InMemoryVIGraph()
    control = _node(vi, vi, [Terminal(id="ctl", index=0, direction="output")])
    first = _node(
        "first",
        vi,
        [
            Terminal(id="first:in", index=0, direction="input"),
            Terminal(id="first:out", index=1, direction="output"),
        ],
    )
    second = _node(
        "second",
        vi,
        [
            Terminal(id="second:in", index=0, direction="input"),
            Terminal(id="second:out", index=1, direction="output"),
        ],
    )
    nodes = [control, first, second]
    for n in nodes:
        graph._graph.add_node(n.id, node=n)  # noqa: SLF001
        for t in n.terminals:
            graph._term_to_node[t.id] = n.id  # noqa: SLF001
    for source, dest in (
        ("ctl", "first:in"),
        ("first:out", "second:in"),
        ("second:out", "ctl"),
    ):
        src_node = graph._term_to_node[source]  # noqa: SLF001
        dst_node = graph._term_to_node[dest]  # noqa: SLF001
        graph._graph.add_edge(  # noqa: SLF001
            src_node,
            dst_node,
            source=WireEnd(terminal_id=source, node_id=src_node),
            dest=WireEnd(terminal_id=dest, node_id=dst_node),
        )
    ctx = CodeGenContext(graph=graph)
    ctx.vi_name = vi

    tiers = topological_sort_tiered([first, second], ctx)

    assert [[n.id for n in tier] for tier in tiers] == [["first"], ["second"]]
