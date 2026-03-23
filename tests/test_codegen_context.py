"""Tests for CodeGenContext graph-based resolution."""

from __future__ import annotations

import pytest

from vipy.agent.codegen.context import CodeGenContext
from vipy.graph_types import WireEnd
from vipy.memory_graph import InMemoryVIGraph


def _make_graph_with_edge(src_tid, dst_tid, src_node="p1", dst_node="p2"):
    """Helper: create graph with one edge between two nodes."""
    graph = InMemoryVIGraph()
    graph._graph.add_node(src_node, node=None)
    graph._graph.add_node(dst_node, node=None)
    graph._graph.add_edge(
        src_node, dst_node,
        source=WireEnd(terminal_id=src_tid, node_id=src_node),
        dest=WireEnd(terminal_id=dst_tid, node_id=dst_node),
    )
    graph._term_to_node[src_tid] = src_node
    graph._term_to_node[dst_tid] = dst_node
    return graph


class TestGraphResolution:
    """Tests for resolve() walking the graph."""

    def test_resolve_direct_binding(self):
        ctx = CodeGenContext()
        ctx.bind("t1", "my_var")
        assert ctx.resolve("t1") == "my_var"

    def test_resolve_through_edge(self):
        graph = _make_graph_with_edge("src", "dest")
        ctx = CodeGenContext(graph=graph)
        ctx.bind("src", "my_var")
        assert ctx.resolve("dest") == "my_var"

    def test_resolve_chain(self):
        graph = InMemoryVIGraph()
        for n in ["p1", "p2", "p3"]:
            graph._graph.add_node(n, node=None)
        graph._graph.add_edge("p1", "p2",
            source=WireEnd(terminal_id="a", node_id="p1"),
            dest=WireEnd(terminal_id="b", node_id="p2"))
        graph._graph.add_edge("p2", "p3",
            source=WireEnd(terminal_id="c", node_id="p2"),
            dest=WireEnd(terminal_id="d", node_id="p3"))
        graph._term_to_node.update({"a": "p1", "b": "p2", "c": "p2", "d": "p3"})

        ctx = CodeGenContext(graph=graph)
        ctx.bind("a", "origin")
        # d <- c <- (p2 has no binding for c, but c is on p2)
        # Actually c is a different terminal than b. No edge from b to c.
        # So d cannot trace back to a. This is correct.
        assert ctx.resolve("d") is None

    def test_resolve_cycle_detection(self):
        graph = InMemoryVIGraph()
        graph._graph.add_node("p1", node=None)
        graph._graph.add_node("p2", node=None)
        graph._graph.add_edge("p1", "p2",
            source=WireEnd(terminal_id="a", node_id="p1"),
            dest=WireEnd(terminal_id="b", node_id="p2"))
        graph._graph.add_edge("p2", "p1",
            source=WireEnd(terminal_id="b", node_id="p2"),
            dest=WireEnd(terminal_id="a", node_id="p1"))
        graph._term_to_node.update({"a": "p1", "b": "p2"})

        ctx = CodeGenContext(graph=graph)
        assert ctx.resolve("a") is None  # cycle, no binding

    def test_resolve_none_binding_treated_as_unresolved(self):
        ctx = CodeGenContext()
        ctx.bind("t1", "None")
        assert ctx.resolve("t1") is None

    def test_resolve_no_graph(self):
        ctx = CodeGenContext()
        assert ctx.resolve("t1") is None

    def test_is_wired(self):
        graph = _make_graph_with_edge("src", "dest")
        ctx = CodeGenContext(graph=graph)
        assert ctx.is_wired("src") is True
        assert ctx.is_wired("dest") is True
        assert ctx.is_wired("nonexistent") is False

    def test_get_source(self):
        graph = _make_graph_with_edge("src", "dest")
        ctx = CodeGenContext(graph=graph)
        source = ctx.get_source("dest")
        assert source is not None
        assert source["src_terminal"] == "src"

    def test_get_destinations(self):
        graph = _make_graph_with_edge("src", "dest")
        ctx = CodeGenContext(graph=graph)
        dests = ctx.get_destinations("src")
        assert len(dests) == 1
        assert dests[0]["dest_terminal"] == "dest"


class TestContextOperations:
    """Tests for bind, merge, child."""

    def test_bind_and_resolve(self):
        ctx = CodeGenContext()
        ctx.bind("t1", "x")
        assert ctx.resolve("t1") == "x"

    def test_merge(self):
        ctx = CodeGenContext()
        ctx.merge({"t1": "a", "t2": "b"})
        assert ctx.resolve("t1") == "a"
        assert ctx.resolve("t2") == "b"

    def test_child_inherits_bindings(self):
        ctx = CodeGenContext()
        ctx.bind("t1", "x")
        child = ctx.child()
        assert child.resolve("t1") == "x"

    def test_child_does_not_affect_parent(self):
        ctx = CodeGenContext()
        child = ctx.child()
        child.bind("t1", "x")
        assert ctx.resolve("t1") is None

    def test_loop_index_vars(self):
        ctx = CodeGenContext()
        assert ctx.get_loop_index_var() == "i"
        child = ctx.child(increment_loop_depth=True)
        assert child.get_loop_index_var() == "j"
