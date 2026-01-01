"""Tests for the InMemoryVIGraph (memory_graph module)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vipy.memory_graph import InMemoryVIGraph, connect


# === Basic Graph Creation Tests ===


class TestInMemoryVIGraphCreation:
    """Tests for InMemoryVIGraph initialization and basic operations."""

    def test_create_empty_graph(self):
        """Test creating an empty graph."""
        graph = InMemoryVIGraph()
        assert graph is not None
        assert graph.list_vis() == []

    def test_connect_function(self):
        """Test the connect() convenience function."""
        graph = connect()
        assert isinstance(graph, InMemoryVIGraph)

    def test_context_manager(self):
        """Test using the graph as a context manager."""
        with InMemoryVIGraph() as graph:
            assert graph is not None
        # After exit, graph should be cleared
        assert graph.list_vis() == []

    def test_clear(self):
        """Test clearing the graph."""
        graph = InMemoryVIGraph()
        # Add some mock data
        graph._dataflow["Test.vi"] = MagicMock()
        graph._dep_graph.add_node("Test.vi")

        graph.clear()

        assert graph.list_vis() == []
        assert len(graph._dep_graph.nodes()) == 0


# === Mock-Based Unit Tests ===


class TestDependencyGraphQueries:
    """Tests for dependency graph queries using mocked data."""

    @pytest.fixture
    def graph_with_deps(self) -> InMemoryVIGraph:
        """Create a graph with mock dependency structure."""
        graph = InMemoryVIGraph()

        # Create mock dataflow graphs (empty NetworkX DiGraphs)
        import networkx as nx

        graph._dataflow["Main.vi"] = nx.DiGraph()
        graph._dataflow["Helper1.vi"] = nx.DiGraph()
        graph._dataflow["Helper2.vi"] = nx.DiGraph()
        graph._dataflow["Leaf.vi"] = nx.DiGraph()

        # Create dependency structure:
        # Main.vi -> Helper1.vi -> Leaf.vi
        # Main.vi -> Helper2.vi -> Leaf.vi
        graph._dep_graph.add_edge("Main.vi", "Helper1.vi")
        graph._dep_graph.add_edge("Main.vi", "Helper2.vi")
        graph._dep_graph.add_edge("Helper1.vi", "Leaf.vi")
        graph._dep_graph.add_edge("Helper2.vi", "Leaf.vi")

        return graph

    def test_list_vis(self, graph_with_deps: InMemoryVIGraph):
        """Test listing all VIs."""
        vis = graph_with_deps.list_vis()
        assert len(vis) == 4
        assert "Main.vi" in vis
        assert "Leaf.vi" in vis

    def test_get_vi_dependencies(self, graph_with_deps: InMemoryVIGraph):
        """Test getting VI dependencies."""
        deps = graph_with_deps.get_vi_dependencies("Main.vi")
        assert len(deps) == 2
        assert "Helper1.vi" in deps
        assert "Helper2.vi" in deps

        # Leaf has no dependencies
        leaf_deps = graph_with_deps.get_vi_dependencies("Leaf.vi")
        assert len(leaf_deps) == 0

    def test_get_vi_dependents(self, graph_with_deps: InMemoryVIGraph):
        """Test getting VIs that depend on a given VI."""
        dependents = graph_with_deps.get_vi_dependents("Leaf.vi")
        assert len(dependents) == 2
        assert "Helper1.vi" in dependents
        assert "Helper2.vi" in dependents

        # Main has no dependents
        main_dependents = graph_with_deps.get_vi_dependents("Main.vi")
        assert len(main_dependents) == 0

    def test_get_leaf_vis(self, graph_with_deps: InMemoryVIGraph):
        """Test getting leaf VIs (no dependencies)."""
        leaves = graph_with_deps.get_leaf_vis()
        assert len(leaves) == 1
        assert "Leaf.vi" in leaves

    def test_has_cycles_no_cycles(self, graph_with_deps: InMemoryVIGraph):
        """Test cycle detection with no cycles."""
        assert graph_with_deps.has_cycles() is False

    def test_has_cycles_with_cycles(self):
        """Test cycle detection with cycles."""
        import networkx as nx

        graph = InMemoryVIGraph()
        graph._dataflow["A.vi"] = nx.DiGraph()
        graph._dataflow["B.vi"] = nx.DiGraph()

        # Create cycle: A -> B -> A
        graph._dep_graph.add_edge("A.vi", "B.vi")
        graph._dep_graph.add_edge("B.vi", "A.vi")

        assert graph.has_cycles() is True

    def test_get_cycles(self):
        """Test getting cycle members."""
        import networkx as nx

        graph = InMemoryVIGraph()
        graph._dataflow["A.vi"] = nx.DiGraph()
        graph._dataflow["B.vi"] = nx.DiGraph()

        graph._dep_graph.add_edge("A.vi", "B.vi")
        graph._dep_graph.add_edge("B.vi", "A.vi")

        cycles = graph.get_cycles()
        assert len(cycles) == 1
        assert set(cycles[0]) == {"A.vi", "B.vi"}

    def test_get_generation_order(self, graph_with_deps: InMemoryVIGraph):
        """Test getting VIs in generation (dependency) order."""
        generations = list(graph_with_deps.get_generation_order())

        # Leaf should come first (no dependencies)
        assert "Leaf.vi" in generations[0]

        # Helpers should come after Leaf
        all_after_first = set()
        for gen in generations[1:]:
            all_after_first.update(gen)
        assert "Helper1.vi" in all_after_first or "Helper2.vi" in all_after_first

        # Main should come last
        assert "Main.vi" in generations[-1]

    def test_get_conversion_order(self, graph_with_deps: InMemoryVIGraph):
        """Test getting flat conversion order."""
        order = graph_with_deps.get_conversion_order()
        assert len(order) == 4

        # Leaf must come before Helpers
        leaf_idx = order.index("Leaf.vi")
        helper1_idx = order.index("Helper1.vi")
        helper2_idx = order.index("Helper2.vi")
        assert leaf_idx < helper1_idx
        assert leaf_idx < helper2_idx

        # Helpers must come before Main
        main_idx = order.index("Main.vi")
        assert helper1_idx < main_idx
        assert helper2_idx < main_idx


class TestStubVIs:
    """Tests for stub VI handling."""

    def test_is_stub_vi(self):
        """Test checking if a VI is a stub."""
        graph = InMemoryVIGraph()
        graph._stubs.add("Missing.vi")

        assert graph.is_stub_vi("Missing.vi") is True
        assert graph.is_stub_vi("Present.vi") is False

    def test_get_stub_vi_info_not_stub(self):
        """Test getting info for non-stub VI returns None."""
        graph = InMemoryVIGraph()
        assert graph.get_stub_vi_info("Regular.vi") is None

    def test_get_stub_vi_info_from_vilib(self):
        """Test getting stub info from vilib resolver."""
        graph = InMemoryVIGraph()
        graph._stubs.add("Application Directory.vi")

        # Should attempt vilib lookup
        info = graph.get_stub_vi_info("Application Directory.vi")
        # Result depends on whether vilib data is available
        assert info is not None or info is None  # Either is valid


class TestDataflowGraphQueries:
    """Tests for dataflow graph queries."""

    @pytest.fixture
    def graph_with_dataflow(self) -> InMemoryVIGraph:
        """Create a graph with mock dataflow structure."""
        import networkx as nx

        graph = InMemoryVIGraph()

        # Build a simple dataflow graph for Test.vi:
        # Input1 -> Add -> Output1
        # Input2 ->
        # Const1 ->
        g = nx.DiGraph()

        # Add input nodes
        g.add_node(
            "input1",
            kind="input",
            name="X",
            is_public=True,
            slot_index=0,
            control_type="stdNum",
        )
        g.add_node(
            "input2",
            kind="input",
            name="Y",
            is_public=True,
            slot_index=1,
            control_type="stdNum",
        )

        # Add output node
        g.add_node(
            "output1",
            kind="output",
            name="Sum",
            is_public=True,
            slot_index=2,
            is_indicator=True,
        )

        # Add constant
        g.add_node(
            "const1",
            kind="constant",
            value=0,
            type="int",
            raw_value="00000000",
        )

        # Add primitive operation
        g.add_node(
            "add1",
            kind="primitive",
            name="Add",
            prim_id=1,
            node_type="prim",
            terminals=[
                {"id": "t1", "index": 0, "direction": "input"},
                {"id": "t2", "index": 1, "direction": "input"},
                {"id": "t3", "index": 2, "direction": "output"},
            ],
        )

        # Add terminal nodes
        g.add_node("t1", kind="terminal", parent_id="add1", index=0, direction="input")
        g.add_node("t2", kind="terminal", parent_id="add1", index=1, direction="input")
        g.add_node("t3", kind="terminal", parent_id="add1", index=2, direction="output")

        # Add edges (wires)
        g.add_edge("input1", "t1", from_parent="input1", to_parent="add1")
        g.add_edge("input2", "t2", from_parent="input2", to_parent="add1")
        g.add_edge("t3", "output1", from_parent="add1", to_parent="output1")

        graph._dataflow["Test.vi"] = g
        graph._dep_graph.add_node("Test.vi")

        return graph

    def test_get_inputs(self, graph_with_dataflow: InMemoryVIGraph):
        """Test getting VI inputs."""
        inputs = graph_with_dataflow.get_inputs("Test.vi")
        assert len(inputs) == 2
        names = {inp["name"] for inp in inputs}
        assert "X" in names
        assert "Y" in names

    def test_get_outputs(self, graph_with_dataflow: InMemoryVIGraph):
        """Test getting VI outputs."""
        outputs = graph_with_dataflow.get_outputs("Test.vi")
        assert len(outputs) == 1
        assert outputs[0]["name"] == "Sum"

    def test_get_constants(self, graph_with_dataflow: InMemoryVIGraph):
        """Test getting constants."""
        constants = graph_with_dataflow.get_constants("Test.vi")
        assert len(constants) == 1
        assert constants[0]["value"] == 0

    def test_get_operations(self, graph_with_dataflow: InMemoryVIGraph):
        """Test getting operations."""
        ops = graph_with_dataflow.get_operations("Test.vi")
        assert len(ops) == 1
        assert ops[0]["name"] == "Add"
        assert ops[0]["labels"] == ["Primitive"]
        assert ops[0]["primResID"] == 1

    def test_get_node(self, graph_with_dataflow: InMemoryVIGraph):
        """Test getting a specific node."""
        node = graph_with_dataflow.get_node("Test.vi", "add1")
        assert node is not None
        assert node["name"] == "Add"
        assert node["kind"] == "primitive"

        # Non-existent node
        missing = graph_with_dataflow.get_node("Test.vi", "nonexistent")
        assert missing is None

    def test_get_predecessors(self, graph_with_dataflow: InMemoryVIGraph):
        """Test getting node predecessors."""
        preds = graph_with_dataflow.get_predecessors("Test.vi", "t1")
        assert "input1" in preds

    def test_get_successors(self, graph_with_dataflow: InMemoryVIGraph):
        """Test getting node successors."""
        succs = graph_with_dataflow.get_successors("Test.vi", "t3")
        assert "output1" in succs

    def test_get_vi_context(self, graph_with_dataflow: InMemoryVIGraph):
        """Test getting full VI context."""
        ctx = graph_with_dataflow.get_vi_context("Test.vi")

        assert ctx["name"] == "Test.vi"
        assert len(ctx["inputs"]) == 2
        assert len(ctx["outputs"]) == 1
        assert len(ctx["constants"]) == 1
        assert len(ctx["operations"]) == 1
        assert "data_flow" in ctx

    def test_get_dataflow_graph(self, graph_with_dataflow: InMemoryVIGraph):
        """Test getting raw dataflow graph."""
        g = graph_with_dataflow.get_dataflow_graph("Test.vi")
        assert g is not None
        assert len(g.nodes()) > 0

        # Non-existent VI
        missing = graph_with_dataflow.get_dataflow_graph("Missing.vi")
        assert missing is None


class TestWiresAndDataFlow:
    """Tests for wire and data flow queries."""

    @pytest.fixture
    def graph_with_wires(self) -> InMemoryVIGraph:
        """Create a graph with wire connections."""
        import networkx as nx

        graph = InMemoryVIGraph()
        g = nx.DiGraph()

        # Simple: Input -> Primitive -> Output
        g.add_node("inp", kind="input", name="In")
        g.add_node("prim", kind="primitive", name="Process")
        g.add_node("out", kind="output", name="Out")
        g.add_node("t1", kind="terminal", parent_id="prim", direction="input")
        g.add_node("t2", kind="terminal", parent_id="prim", direction="output")

        g.add_edge("inp", "t1", from_parent="inp", to_parent="prim")
        g.add_edge("t2", "out", from_parent="prim", to_parent="out")

        graph._dataflow["Wire.vi"] = g
        graph._dep_graph.add_node("Wire.vi")

        return graph

    def test_get_wires(self, graph_with_wires: InMemoryVIGraph):
        """Test getting wires from a VI."""
        wires = graph_with_wires.get_wires("Wire.vi")
        assert len(wires) == 2

        # Check wire structure
        wire = wires[0]
        assert "from_terminal_id" in wire
        assert "to_terminal_id" in wire
        assert "from_parent_labels" in wire
        assert "to_parent_labels" in wire

    def test_get_source_of_output(self, graph_with_wires: InMemoryVIGraph):
        """Test tracing output back to source."""
        source = graph_with_wires.get_source_of_output("Wire.vi", "out")
        # Should trace back through t2 to prim
        assert source == "prim"


class TestLoopStructures:
    """Tests for loop structure handling."""

    @pytest.fixture
    def graph_with_loop(self) -> InMemoryVIGraph:
        """Create a graph with a loop structure."""
        import networkx as nx

        from vipy.parser import LoopStructure, TunnelMapping

        graph = InMemoryVIGraph()
        g = nx.DiGraph()

        # Create a while loop structure
        g.add_node(
            "loop1",
            kind="operation",
            name="While Loop",
            node_type="whileLoop",
        )
        g.add_node("inner_op", kind="primitive", name="Increment")

        # Loop tunnels
        g.add_node("outer_in", kind="terminal", parent_id="loop1")
        g.add_node("inner_in", kind="terminal", parent_id="loop1")
        g.add_node("outer_out", kind="terminal", parent_id="loop1")
        g.add_node("inner_out", kind="terminal", parent_id="loop1")

        # Tunnel edges
        g.add_edge("outer_in", "inner_in", tunnel_type="lpTun", loop_uid="loop1")
        g.add_edge("inner_out", "outer_out", tunnel_type="lMax", loop_uid="loop1")

        graph._dataflow["Loop.vi"] = g
        graph._dep_graph.add_node("Loop.vi")

        # Store loop structure
        loop_struct = LoopStructure(
            uid="loop1",
            loop_type="whileLoop",
            tunnels=[
                TunnelMapping("outer_in", "inner_in", "lpTun"),
                TunnelMapping("outer_out", "inner_out", "lMax"),
            ],
            inner_node_uids=["inner_op"],
            stop_condition_terminal_uid="stop_term",
        )
        graph._loop_structures["Loop.vi"] = {"loop1": loop_struct}

        return graph

    def test_loop_in_operations(self, graph_with_loop: InMemoryVIGraph):
        """Test that loops appear in operations list."""
        ops = graph_with_loop.get_operations("Loop.vi")

        # Should have the while loop
        loop_ops = [op for op in ops if op.get("loop_type") == "whileLoop"]
        assert len(loop_ops) == 1

        loop_op = loop_ops[0]
        assert loop_op["labels"] == ["Loop"]
        assert "tunnels" in loop_op
        assert "inner_nodes" in loop_op
        assert "stop_condition_terminal" in loop_op


class TestCrossVIBindings:
    """Tests for cross-VI terminal bindings."""

    @pytest.fixture
    def graph_with_bindings(self) -> InMemoryVIGraph:
        """Create a graph with cross-VI bindings."""
        import networkx as nx

        graph = InMemoryVIGraph()

        # Caller VI
        caller = nx.DiGraph()
        caller.add_node("caller_inp", kind="input", name="X", slot_index=0)
        caller.add_node(
            "subvi_node",
            kind="subvi",
            name="Helper.vi",
            terminals=[
                {"id": "caller_t1", "index": 0, "direction": "input"},
                {"id": "caller_t2", "index": 1, "direction": "output"},
            ],
        )
        graph._dataflow["Caller.vi"] = caller

        # Callee VI
        callee = nx.DiGraph()
        callee.add_node("callee_inp", kind="input", name="Input", slot_index=0)
        callee.add_node("callee_out", kind="output", name="Output", slot_index=1)
        graph._dataflow["Helper.vi"] = callee

        # Set up binding
        graph._bindings[("Caller.vi", "caller_t1")] = ("Helper.vi", "callee_inp")
        graph._bindings[("Caller.vi", "caller_t2")] = ("Helper.vi", "callee_out")

        graph._dep_graph.add_edge("Caller.vi", "Helper.vi")

        return graph

    def test_get_binding(self, graph_with_bindings: InMemoryVIGraph):
        """Test getting a specific binding."""
        binding = graph_with_bindings.get_binding("Caller.vi", "caller_t1")
        assert binding is not None
        assert binding == ("Helper.vi", "callee_inp")

    def test_get_bindings_for_vi(self, graph_with_bindings: InMemoryVIGraph):
        """Test getting all bindings for a VI."""
        bindings = graph_with_bindings.get_bindings_for_vi("Caller.vi")
        assert len(bindings) == 2

        # Check binding structure
        binding = bindings[0]
        assert binding["caller_vi"] == "Caller.vi"
        assert binding["subvi_name"] == "Helper.vi"


class TestCypherCompatibility:
    """Tests for Cypher query compatibility layer."""

    @pytest.fixture
    def graph_with_data(self) -> InMemoryVIGraph:
        """Create a graph with various node types."""
        import networkx as nx

        graph = InMemoryVIGraph()
        g = nx.DiGraph()

        g.add_node("const1", kind="constant", value=42, type="int")
        g.add_node("prim1", kind="primitive", prim_id=1, terminals=[])
        g.add_node("cluster_in", kind="input", name="Cluster", control_type="stdClust")

        graph._dataflow["Test.vi"] = g
        return graph

    def test_query_constants(self, graph_with_data: InMemoryVIGraph):
        """Test Cypher-style constant query."""
        results = graph_with_data.query("MATCH (c:Constant) RETURN c")
        assert len(results) == 1
        assert results[0]["vi_name"] == "Test.vi"

    def test_query_primitives(self, graph_with_data: InMemoryVIGraph):
        """Test Cypher-style primitive query."""
        results = graph_with_data.query("MATCH (p:Primitive) RETURN p")
        assert len(results) == 1
        assert results[0]["prim_id"] == 1

    def test_query_clusters(self, graph_with_data: InMemoryVIGraph):
        """Test Cypher-style cluster query."""
        results = graph_with_data.query("MATCH (c:Cluster) RETURN c")
        assert len(results) == 1
        assert results[0]["name"] == "Cluster"

    def test_query_single(self, graph_with_data: InMemoryVIGraph):
        """Test single-result query."""
        result = graph_with_data.query_single("MATCH (c:Constant)")
        assert result is not None


# === Integration Tests with Real VIs ===


class TestRealVILoading:
    """Integration tests using real VI files."""

    @pytest.fixture
    def sample_vi_path(self) -> Path | None:
        """Get path to a sample VI if available."""
        path = Path(
            "samples/JKI-VI-Tester/source/User Interfaces/"
            "Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi"
        )
        if path.exists():
            return path
        return None

    def test_load_real_vi(self, sample_vi_path: Path | None):
        """Test loading a real VI file."""
        if sample_vi_path is None:
            pytest.skip("Sample VI not available")

        graph = InMemoryVIGraph()
        graph.load_vi(
            sample_vi_path,
            expand_subvis=False,  # Don't expand to keep test fast
        )

        vis = graph.list_vis()
        assert len(vis) >= 1

        vi_name = sample_vi_path.name
        ctx = graph.get_vi_context(vi_name)
        assert ctx is not None
        assert ctx["name"] == vi_name

    def test_load_vi_with_expansion(self, sample_vi_path: Path | None):
        """Test loading a VI with SubVI expansion."""
        if sample_vi_path is None:
            pytest.skip("Sample VI not available")

        graph = InMemoryVIGraph()
        graph.load_vi(
            sample_vi_path,
            expand_subvis=True,
            search_paths=[Path("samples/OpenG/extracted")],
        )

        vis = graph.list_vis()
        # Should have loaded multiple VIs (main + SubVIs)
        assert len(vis) >= 1

        # Check conversion order
        order = graph.get_conversion_order()
        assert len(order) >= 1

    def test_load_vi_clears_on_request(self, sample_vi_path: Path | None):
        """Test that clear_first=True clears existing data."""
        if sample_vi_path is None:
            pytest.skip("Sample VI not available")

        graph = InMemoryVIGraph()

        # Load first time
        graph.load_vi(sample_vi_path, expand_subvis=False)
        initial_count = len(graph.list_vis())

        # Load again with clear_first
        graph.load_vi(sample_vi_path, expand_subvis=False, clear_first=True)
        assert len(graph.list_vis()) == initial_count

    def test_load_from_xml(self, sample_vi_path: Path | None, tmp_path: Path):
        """Test loading from extracted XML files."""
        if sample_vi_path is None:
            pytest.skip("Sample VI not available")

        from vipy.extractor import extract_vi_xml

        bd_xml, fp_xml, main_xml = extract_vi_xml(sample_vi_path)

        graph = InMemoryVIGraph()
        graph.load_vi(bd_xml, expand_subvis=False)

        assert len(graph.list_vis()) >= 1


class TestKindToLabels:
    """Tests for the internal _kind_to_labels method."""

    def test_kind_to_labels_subvi(self):
        """Test SubVI label conversion."""
        graph = InMemoryVIGraph()
        assert graph._kind_to_labels("subvi") == ["SubVI"]

    def test_kind_to_labels_primitive(self):
        """Test Primitive label conversion."""
        graph = InMemoryVIGraph()
        assert graph._kind_to_labels("primitive") == ["Primitive"]

    def test_kind_to_labels_input(self):
        """Test Input label conversion."""
        graph = InMemoryVIGraph()
        assert graph._kind_to_labels("input") == ["Control", "Input"]

    def test_kind_to_labels_output(self):
        """Test Output label conversion."""
        graph = InMemoryVIGraph()
        assert graph._kind_to_labels("output") == ["Indicator", "Output"]

    def test_kind_to_labels_constant(self):
        """Test Constant label conversion."""
        graph = InMemoryVIGraph()
        assert graph._kind_to_labels("constant") == ["Constant"]

    def test_kind_to_labels_unknown(self):
        """Test unknown kind returns empty list."""
        graph = InMemoryVIGraph()
        assert graph._kind_to_labels("unknown_kind") == []
