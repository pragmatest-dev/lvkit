"""Tests for InMemoryVIGraph."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph, connect
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import ConstantNode, PrimitiveNode, VINode, WireEnd
from lvkit.models import FPTerminal, Terminal


class TestInMemoryVIGraphCreation:
    """Tests for InMemoryVIGraph initialization and basic operations."""

    def test_create_empty_graph(self):
        graph = InMemoryVIGraph()
        assert graph.list_vis() == []

    def test_connect_function(self):
        graph = connect()
        assert isinstance(graph, InMemoryVIGraph)

    def test_context_manager(self):
        with InMemoryVIGraph() as graph:
            assert graph is not None
        assert graph.list_vis() == []

    def test_clear(self):
        graph = InMemoryVIGraph()
        graph._dep_graph.add_node("Test.vi")
        graph._vi_nodes["Test.vi"] = {"node1"}
        graph.clear()
        assert graph.list_vis() == []
        assert len(graph._dep_graph.nodes()) == 0


class TestDependencyGraphQueries:
    """Tests for dependency graph queries."""

    @pytest.fixture
    def graph_with_deps(self) -> InMemoryVIGraph:
        graph = InMemoryVIGraph()
        for name in ["Main.vi", "Helper1.vi", "Helper2.vi", "Leaf.vi"]:
            graph._vi_nodes[name] = set()
        graph._dep_graph.add_edge("Main.vi", "Helper1.vi")
        graph._dep_graph.add_edge("Main.vi", "Helper2.vi")
        graph._dep_graph.add_edge("Helper1.vi", "Leaf.vi")
        graph._dep_graph.add_edge("Helper2.vi", "Leaf.vi")
        return graph

    def test_list_vis(self, graph_with_deps: InMemoryVIGraph):
        vis = graph_with_deps.list_vis()
        assert len(vis) == 4
        assert "Main.vi" in vis

    def test_get_vi_dependencies(self, graph_with_deps: InMemoryVIGraph):
        deps = graph_with_deps.get_vi_dependencies("Main.vi")
        assert set(deps) == {"Helper1.vi", "Helper2.vi"}
        assert graph_with_deps.get_vi_dependencies("Leaf.vi") == []

    def test_get_vi_dependents(self, graph_with_deps: InMemoryVIGraph):
        dependents = graph_with_deps.get_vi_dependents("Leaf.vi")
        assert set(dependents) == {"Helper1.vi", "Helper2.vi"}

    def test_get_leaf_vis(self, graph_with_deps: InMemoryVIGraph):
        leaves = graph_with_deps.get_leaf_vis()
        assert leaves == ["Leaf.vi"]

    def test_has_cycles_false(self, graph_with_deps: InMemoryVIGraph):
        assert graph_with_deps.has_cycles() is False

    def test_has_cycles_true(self):
        graph = InMemoryVIGraph()
        for name in ["A.vi", "B.vi"]:
            graph._vi_nodes[name] = set()
        graph._dep_graph.add_edge("A.vi", "B.vi")
        graph._dep_graph.add_edge("B.vi", "A.vi")
        assert graph.has_cycles() is True

    def test_get_generation_order(self, graph_with_deps: InMemoryVIGraph):
        generations = list(graph_with_deps.get_generation_order())
        assert "Leaf.vi" in generations[0]
        assert "Main.vi" in generations[-1]

    def test_get_conversion_order(self, graph_with_deps: InMemoryVIGraph):
        order = graph_with_deps.get_conversion_order()
        assert order.index("Leaf.vi") < order.index("Helper1.vi")
        assert order.index("Leaf.vi") < order.index("Helper2.vi")
        assert order.index("Helper1.vi") < order.index("Main.vi")


class TestTypedGraphNodes:
    """Tests for typed Pydantic node storage."""

    @pytest.fixture
    def graph_with_nodes(self) -> InMemoryVIGraph:
        graph = InMemoryVIGraph()
        vi_name = "Test.vi"

        # VINode with FP terminals — node ID = vi_name
        vi_node = VINode(
            id=vi_name,
            vi=vi_name,
            name="Test.vi",
            terminals=[
                FPTerminal(
                    id="fp_in",
                    index=0,
                    direction="input",
                    name="X",
                    wiring_rule=1,
                    is_public=True,
                ),
                FPTerminal(
                    id="fp_out",
                    index=1,
                    direction="output",
                    name="Sum",
                    wiring_rule=0,
                    is_indicator=True,
                    is_public=True,
                ),
            ],
        )
        graph._graph.add_node(vi_name, node=vi_node)

        # Constant
        const_node = ConstantNode(
            id="const1",
            vi=vi_name,
            value=42,
            label="MyConst",
            terminals=[Terminal(id="const1", index=0, direction="output")],
        )
        graph._graph.add_node("const1", node=const_node)

        # Primitive
        prim_node = PrimitiveNode(
            id="add1",
            vi=vi_name,
            name="Add",
            node_type="prim",
            prim_id=1,
            terminals=[
                Terminal(id="t1", index=0, direction="input"),
                Terminal(id="t2", index=1, direction="input"),
                Terminal(id="t3", index=2, direction="output"),
            ],
        )
        graph._graph.add_node("add1", node=prim_node)

        # Edges with typed WireEnd
        src_fp = WireEnd(
            terminal_id="fp_in",
            node_id=vi_name,
            index=0,
            name="X",
            parent_kind="vi",
        )
        dst_t1 = WireEnd(
            terminal_id="t1", node_id="add1", index=0, parent_kind="primitive"
        )
        graph._graph.add_edge(vi_name, "add1", source=src_fp, dest=dst_t1, vi=vi_name)

        src_const = WireEnd(
            terminal_id="const1", node_id="const1", index=0, parent_kind="constant"
        )
        dst_t2 = WireEnd(
            terminal_id="t2", node_id="add1", index=1, parent_kind="primitive"
        )
        graph._graph.add_edge(
            "const1", "add1", source=src_const, dest=dst_t2, vi=vi_name
        )

        src_t3 = WireEnd(
            terminal_id="t3", node_id="add1", index=2, parent_kind="primitive"
        )
        dst_fp_out = WireEnd(
            terminal_id="fp_out",
            node_id=vi_name,
            index=1,
            name="Sum",
            parent_kind="vi",
        )
        graph._graph.add_edge(
            "add1", vi_name, source=src_t3, dest=dst_fp_out, vi=vi_name
        )

        graph._vi_nodes[vi_name] = {vi_name, "const1", "add1"}
        graph._dep_graph.add_node(vi_name)
        return graph

    def test_get_inputs(self, graph_with_nodes: InMemoryVIGraph):
        inputs = graph_with_nodes.get_inputs("Test.vi")
        assert len(inputs) == 1
        assert inputs[0].name == "X"

    def test_get_outputs(self, graph_with_nodes: InMemoryVIGraph):
        outputs = graph_with_nodes.get_outputs("Test.vi")
        assert len(outputs) == 1
        assert outputs[0].name == "Sum"

    def test_get_constants(self, graph_with_nodes: InMemoryVIGraph):
        constants = graph_with_nodes.get_constants("Test.vi")
        assert len(constants) == 1
        assert constants[0].value == 42

    def test_get_wires(self, graph_with_nodes: InMemoryVIGraph):
        wires = graph_with_nodes.get_wires("Test.vi")
        assert len(wires) == 3
        # All wires should have typed source/dest
        for wire in wires:
            assert wire.source.terminal_id
            assert wire.dest.terminal_id

    def test_resolve_name(self, graph_with_nodes: InMemoryVIGraph):
        # Primitive terminal name
        name = graph_with_nodes.resolve_name("add1", 0)
        assert name is None  # unnamed terminal

    def test_vi_context(self, graph_with_nodes: InMemoryVIGraph):
        ctx = graph_with_nodes.get_vi_context("Test.vi")
        assert ctx.name == "Test.vi"
        assert len(ctx.inputs) == 1
        assert len(ctx.outputs) == 1
        assert len(ctx.constants) == 1
        assert ctx.data_flow is not None


class TestRealVILoading:
    """Integration tests using real VI files."""

    @pytest.fixture
    def sample_vi_path(self) -> Path | None:
        path = Path(
            ".lvkit/cache/samples/JKI-VI-Tester/source/User Interfaces/"
            "Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi"
        )
        return path if path.exists() else None

    def test_load_real_vi(self, sample_vi_path: Path | None):
        if sample_vi_path is None:
            pytest.skip("Sample VI not available")

        graph = InMemoryVIGraph()
        graph.load_vi(sample_vi_path, mode=LoadMode.NONE)

        vis = graph.list_vis()
        assert len(vis) >= 1

        vi_name = vis[0]
        ctx = graph.get_vi_context(vi_name)
        assert ctx is not None

    def test_load_vi_with_expansion(self, sample_vi_path: Path | None):
        if sample_vi_path is None:
            pytest.skip("Sample VI not available")

        graph = InMemoryVIGraph()
        graph.load_vi(
            sample_vi_path,
            mode=LoadMode.FULL,
            search_paths=[Path(".lvkit/cache/samples/OpenG/extracted")],
        )
        assert len(graph.list_vis()) >= 1
        assert len(graph.get_conversion_order()) >= 1


_SAMPLES_ROOT = Path(__file__).parent.parent / ".lvkit" / "cache" / "samples"
_VI_TESTER_ABOUT = (
    _SAMPLES_ROOT
    / "JKI-VI-Tester/source/User Interfaces/Graphical Test Runner"
    / "Graphical Test Runner Support"
    / "VI Tester About.vi"
)


@pytest.mark.skipif(
    not _VI_TESTER_ABOUT.exists(),
    reason="VI Tester About.vi sample not present",
)
class TestSrnClumpNoFabricatedEdges:
    """An sRN is an execution clump (scheduler grouping) that aggregates
    unrelated terminals; it must never be index-paired into fabricated tunnel
    edges. Real inner<->outer pass-throughs come from parser_tunnels' explicit
    uid pairs, not from an sRN's meaningless slot-index collisions.

    Regression guard for VI Tester About, where the removed index-pairing wired
    the copyrights boolean INDICATOR (fPTerm 1516) across two structure borders,
    and produced type-impossible edges (String->Boolean, Refnum->Boolean)."""

    def _graph(self) -> tuple[InMemoryVIGraph, str]:
        graph = InMemoryVIGraph()
        graph.load_vi(str(_VI_TESTER_ABOUT), mode=LoadMode.NONE)
        return graph, graph.resolve_vi_name("VI Tester About.vi")

    def test_copyrights_indicator_is_unwired(self):
        graph, vi = self._graph()
        touching = [
            w
            for w in graph.get_wires(vi, include_internal=True)
            if w.source.terminal_id.endswith("::1516")
            or w.dest.terminal_id.endswith("::1516")
        ]
        assert touching == [], f"fabricated wire on copyrights indicator: {touching}"

    def test_no_srn_pairing_edges(self):
        graph, vi = self._graph()
        srn = [
            e
            for _, _, e in graph._graph.edges(data=True)
            if e.get("vi") == vi and e.get("tunnel_type") == "sRN"
        ]
        assert srn == [], "sRN index-pairing must not fabricate tunnel edges"

    def test_internal_tunnel_edges_are_type_consistent(self):
        """Every reconstructed tunnel pass-through joins same-typed terminals
        (an auto-indexed element<->array tunnel differs structurally, but none
        of this VI's tunnels auto-index — a String<->Boolean join is impossible
        and only ever came from the sRN fabrication)."""
        graph, vi = self._graph()
        bad = []
        for _, _, e in graph._graph.edges(data=True):
            if e.get("vi") != vi or not e.get("tunnel_type"):
                continue
            s, d = e.get("source"), e.get("dest")
            st = graph.get_terminal(s.terminal_id)
            dt = graph.get_terminal(d.terminal_id)
            su = getattr(getattr(st, "lv_type", None), "underlying_type", None)
            du = getattr(getattr(dt, "lv_type", None), "underlying_type", None)
            if su and du and su != du:
                bad.append((s.terminal_id, su, d.terminal_id, du))
        assert bad == [], f"type-mismatched internal edges: {bad}"
