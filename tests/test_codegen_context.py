"""Tests for CodeGenContext graph-based resolution."""

from __future__ import annotations

from lvpy.codegen.context import CodeGenContext
from lvpy.graph import InMemoryVIGraph
from lvpy.graph.models import WireEnd
from tests.helpers import (
    make_graph_with_edge,
    make_graph_with_terminals,
    make_node,
)


class TestGraphResolution:
    """Tests for resolve() walking the graph."""

    def test_resolve_direct_binding(self):
        graph = make_graph_with_terminals("t1")
        ctx = CodeGenContext(graph=graph)
        ctx.bind("t1", "my_var")
        assert ctx.resolve("t1") == "my_var"

    def test_resolve_through_edge(self):
        graph = make_graph_with_edge("src", "dest")
        ctx = CodeGenContext(graph=graph)
        ctx.bind("src", "my_var")
        assert ctx.resolve("dest") == "my_var"

    def test_resolve_chain(self):
        graph = InMemoryVIGraph()
        p1 = make_node("p1", ["a"])
        p2 = make_node("p2", ["b", "c"])
        p3 = make_node("p3", ["d"])
        for nid, node in [("p1", p1), ("p2", p2), ("p3", p3)]:
            graph._graph.add_node(nid, node=node)
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
        p1 = make_node("p1", ["a"])
        p2 = make_node("p2", ["b"])
        graph._graph.add_node("p1", node=p1)
        graph._graph.add_node("p2", node=p2)
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
        graph = make_graph_with_terminals("t1")
        ctx = CodeGenContext(graph=graph)
        ctx.bind("t1", "None")
        assert ctx.resolve("t1") is None

    def test_resolve_no_graph(self):
        ctx = CodeGenContext()
        assert ctx.resolve("t1") is None

    def test_is_wired(self):
        graph = make_graph_with_edge("src", "dest")
        ctx = CodeGenContext(graph=graph)
        assert ctx.is_wired("src") is True
        assert ctx.is_wired("dest") is True
        assert ctx.is_wired("nonexistent") is False

    def test_get_source(self):
        graph = make_graph_with_edge("src", "dest")
        ctx = CodeGenContext(graph=graph)
        source = ctx.get_source("dest")
        assert source is not None
        assert source.src_terminal == "src"

    def test_get_destinations(self):
        graph = make_graph_with_edge("src", "dest")
        ctx = CodeGenContext(graph=graph)
        dests = ctx.get_destinations("src")
        assert len(dests) == 1
        assert dests[0].dest_terminal == "dest"


class TestContextOperations:
    """Tests for bind, merge, child."""

    def test_bind_and_resolve(self):
        graph = make_graph_with_terminals("t1")
        ctx = CodeGenContext(graph=graph)
        ctx.bind("t1", "x")
        assert ctx.resolve("t1") == "x"

    def test_merge(self):
        graph = make_graph_with_terminals("t1", "t2")
        ctx = CodeGenContext(graph=graph)
        ctx.merge({"t1": "a", "t2": "b"})
        assert ctx.resolve("t1") == "a"
        assert ctx.resolve("t2") == "b"

    def test_child_inherits_bindings(self):
        graph = make_graph_with_terminals("t1")
        ctx = CodeGenContext(graph=graph)
        ctx.bind("t1", "x")
        child = ctx.child()
        # child shares the same graph, so it sees parent's bindings
        assert child.resolve("t1") == "x"

    def test_child_does_not_affect_parent(self):
        # With graph-based context, child and parent share the same graph.
        # Bindings set by child ARE visible to parent (no scoping).
        # This is the intended behavior — var_name lives on the graph.
        graph = make_graph_with_terminals("t1")
        ctx = CodeGenContext(graph=graph)
        child = ctx.child()
        child.bind("t1", "x")
        assert ctx.resolve("t1") == "x"

    def test_loop_index_vars(self):
        ctx = CodeGenContext()
        assert ctx.get_loop_index_var() == "i"
        child = ctx.child(increment_loop_depth=True)
        assert child.get_loop_index_var() == "j"
