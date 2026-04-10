"""Shared test helpers for lvpy tests.

Provides graph and context construction for tests that need CodeGenContext
with a proper graph (required since bind/resolve store var_names on the graph).
"""

from __future__ import annotations

from lvpy.agent.codegen.context import CodeGenContext
from lvpy.graph_types import (
    PrimitiveNode,
    Terminal,
    WireEnd,
)
from lvpy.memory_graph import InMemoryVIGraph


def make_node(node_id: str, terminal_ids: list[str]) -> PrimitiveNode:
    """Create a graph node with terminals."""
    return PrimitiveNode(
        id=node_id,
        vi="test.vi",
        name=node_id,
        terminals=[
            Terminal(id=tid, index=i, direction="output")
            for i, tid in enumerate(terminal_ids)
        ],
    )


def make_graph_with_terminals(*terminal_ids: str) -> InMemoryVIGraph:
    """Create a graph with nodes that have the given terminals."""
    graph = InMemoryVIGraph()
    for i, tid in enumerate(terminal_ids):
        nid = f"n{i}"
        node = make_node(nid, [tid])
        graph._graph.add_node(nid, node=node)
        graph._term_to_node[tid] = nid
    return graph


def make_graph_with_edge(
    src_tid: str,
    dst_tid: str,
    src_node: str = "p1",
    dst_node: str = "p2",
) -> InMemoryVIGraph:
    """Create a graph with one edge between two nodes."""
    graph = InMemoryVIGraph()
    src = make_node(src_node, [src_tid])
    dst = make_node(dst_node, [dst_tid])
    graph._graph.add_node(src_node, node=src)
    graph._graph.add_node(dst_node, node=dst)
    graph._graph.add_edge(
        src_node,
        dst_node,
        source=WireEnd(terminal_id=src_tid, node_id=src_node),
        dest=WireEnd(terminal_id=dst_tid, node_id=dst_node),
    )
    graph._term_to_node[src_tid] = src_node
    graph._term_to_node[dst_tid] = dst_node
    return graph


def make_ctx(*terminal_ids: str) -> CodeGenContext:
    """Create a CodeGenContext with a graph that has the given terminals."""
    graph = make_graph_with_terminals(*terminal_ids)
    return CodeGenContext(graph=graph)


