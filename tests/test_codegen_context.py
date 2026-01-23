"""Tests for CodeGenContext reverse flow map and slot_index handling."""

from __future__ import annotations

from vipy.agent.codegen.context import CodeGenContext
from vipy.graph_types import FPTerminalNode, Wire


class TestReverseFlowMap:
    """Tests for the _reverse_flow_map building in CodeGenContext."""

    def test_reverse_flow_map_built_on_init(self):
        """Test that reverse flow map is built during __post_init__."""
        data_flow = [
            Wire(
                from_terminal_id="src1",
                to_terminal_id="dest1",
                from_parent_id="parent1",
            ),
        ]
        ctx = CodeGenContext(data_flow=data_flow)

        # Reverse map should be populated
        assert "src1" in ctx._reverse_flow_map
        dest_list = ctx._reverse_flow_map["src1"]
        assert len(dest_list) == 1
        assert dest_list[0]["dest_terminal"] == "dest1"

    def test_reverse_flow_map_multiple_destinations(self):
        """Test reverse flow map with one source going to multiple destinations."""
        data_flow = [
            Wire(from_terminal_id="src1", to_terminal_id="dest1"),
            Wire(from_terminal_id="src1", to_terminal_id="dest2"),
            Wire(from_terminal_id="src1", to_terminal_id="dest3"),
        ]
        ctx = CodeGenContext(data_flow=data_flow)

        # src1 should have 3 destinations
        assert len(ctx._reverse_flow_map["src1"]) == 3
        dest_terminals = {d["dest_terminal"] for d in ctx._reverse_flow_map["src1"]}
        assert dest_terminals == {"dest1", "dest2", "dest3"}

    def test_reverse_flow_map_includes_parent_info(self):
        """Test that reverse flow map includes destination parent information."""
        data_flow = [
            Wire(
                from_terminal_id="src1",
                to_terminal_id="dest1",
                to_parent_id="parent1",
                to_parent_name="MySubVI.vi",
                to_parent_labels=["SubVI"],
            ),
        ]
        ctx = CodeGenContext(data_flow=data_flow)

        dest_info = ctx._reverse_flow_map["src1"][0]
        assert dest_info["dest_parent_id"] == "parent1"
        assert dest_info["dest_parent_name"] == "MySubVI.vi"
        assert dest_info["dest_parent_labels"] == ["SubVI"]


class TestSlotIndexPropagation:
    """Tests for slot_index handling in flow maps."""

    def test_flow_map_includes_src_slot_index(self):
        """Test that forward flow map includes source slot index."""
        data_flow = [
            Wire(
                from_terminal_id="src1",
                to_terminal_id="dest1",
                from_slot_index=3,
            ),
        ]
        ctx = CodeGenContext(data_flow=data_flow)

        flow_info = ctx._flow_map["dest1"]
        assert flow_info["src_slot_index"] == 3

    def test_flow_map_includes_dest_slot_index(self):
        """Test that reverse flow map includes destination slot index."""
        data_flow = [
            Wire(
                from_terminal_id="src1",
                to_terminal_id="dest1",
                to_slot_index=5,
            ),
        ]
        ctx = CodeGenContext(data_flow=data_flow)

        dest_info = ctx._reverse_flow_map["src1"][0]
        assert dest_info["dest_slot_index"] == 5

    def test_slot_index_none_when_not_set(self):
        """Test that slot indices are None when not set."""
        data_flow = [
            Wire(from_terminal_id="src1", to_terminal_id="dest1"),
        ]
        ctx = CodeGenContext(data_flow=data_flow)

        flow_info = ctx._flow_map["dest1"]
        assert flow_info["src_slot_index"] is None

        dest_info = ctx._reverse_flow_map["src1"][0]
        assert dest_info["dest_slot_index"] is None


class TestResolveWithFlowMap:
    """Tests for resolve() using flow maps."""

    def test_resolve_through_flow_map(self):
        """Test resolving terminal through flow map."""
        data_flow = [
            Wire(from_terminal_id="src1", to_terminal_id="dest1"),
        ]
        ctx = CodeGenContext(data_flow=data_flow)
        ctx.bind("src1", "my_variable")

        # Resolving dest1 should trace back to src1
        resolved = ctx.resolve("dest1")
        assert resolved == "my_variable"

    def test_resolve_through_parent_id(self):
        """Test resolving terminal through parent ID binding."""
        data_flow = [
            Wire(
                from_terminal_id="src_term",
                to_terminal_id="dest1",
                from_parent_id="const1",
            ),
        ]
        ctx = CodeGenContext(data_flow=data_flow)
        ctx.bind("const1", "42")

        # Resolving dest1 should find const1 via parent
        resolved = ctx.resolve("dest1")
        assert resolved == "42"

    def test_resolve_chain_through_multiple_wires(self):
        """Test resolving through a chain of wires."""
        data_flow = [
            Wire(from_terminal_id="src1", to_terminal_id="mid1"),
            Wire(from_terminal_id="mid1", to_terminal_id="dest1"),
        ]
        ctx = CodeGenContext(data_flow=data_flow)
        ctx.bind("src1", "original_value")

        # Resolving dest1 should trace back through mid1 to src1
        resolved = ctx.resolve("dest1")
        assert resolved == "original_value"


class TestWiredTerminals:
    """Tests for wired terminal tracking."""

    def test_is_wired_for_source_terminal(self):
        """Test is_wired returns True for source terminal."""
        data_flow = [
            Wire(from_terminal_id="src1", to_terminal_id="dest1"),
        ]
        ctx = CodeGenContext(data_flow=data_flow)

        assert ctx.is_wired("src1") is True

    def test_is_wired_for_dest_terminal(self):
        """Test is_wired returns True for destination terminal."""
        data_flow = [
            Wire(from_terminal_id="src1", to_terminal_id="dest1"),
        ]
        ctx = CodeGenContext(data_flow=data_flow)

        assert ctx.is_wired("dest1") is True

    def test_is_wired_for_unwired_terminal(self):
        """Test is_wired returns False for unwired terminal."""
        data_flow = [
            Wire(from_terminal_id="src1", to_terminal_id="dest1"),
        ]
        ctx = CodeGenContext(data_flow=data_flow)

        assert ctx.is_wired("other_terminal") is False


class TestLoopIndexVariable:
    """Tests for loop index variable naming."""

    def test_get_loop_index_var_depth_0(self):
        """Test index variable at depth 0."""
        ctx = CodeGenContext(loop_depth=0)
        assert ctx.get_loop_index_var() == "i"

    def test_get_loop_index_var_depth_1(self):
        """Test index variable at depth 1."""
        ctx = CodeGenContext(loop_depth=1)
        assert ctx.get_loop_index_var() == "j"

    def test_get_loop_index_var_depth_5(self):
        """Test index variable at depth 5."""
        ctx = CodeGenContext(loop_depth=5)
        assert ctx.get_loop_index_var() == "n"

    def test_get_loop_index_var_depth_6_and_beyond(self):
        """Test index variable at depth >= 6 uses idx_N format."""
        ctx = CodeGenContext(loop_depth=6)
        assert ctx.get_loop_index_var() == "idx_6"

        ctx = CodeGenContext(loop_depth=10)
        assert ctx.get_loop_index_var() == "idx_10"


class TestChildContext:
    """Tests for child context creation."""

    def test_child_inherits_bindings(self):
        """Test that child context inherits parent bindings."""
        data_flow = [Wire(from_terminal_id="src1", to_terminal_id="dest1")]
        ctx = CodeGenContext(data_flow=data_flow)
        ctx.bind("term1", "var1")

        child = ctx.child()

        assert child.resolve("term1") == "var1"

    def test_child_bindings_dont_affect_parent(self):
        """Test that child bindings don't modify parent."""
        ctx = CodeGenContext()
        ctx.bind("term1", "var1")

        child = ctx.child()
        child.bind("term2", "var2")

        assert ctx.resolve("term2") is None
        assert child.resolve("term2") == "var2"

    def test_child_shares_flow_maps(self):
        """Test that child shares flow maps with parent (read-only)."""
        data_flow = [Wire(from_terminal_id="src1", to_terminal_id="dest1")]
        ctx = CodeGenContext(data_flow=data_flow)

        child = ctx.child()

        # Should be same object
        assert child._flow_map is ctx._flow_map
        assert child._reverse_flow_map is ctx._reverse_flow_map

    def test_child_increment_loop_depth(self):
        """Test child with incremented loop depth."""
        ctx = CodeGenContext(loop_depth=1)

        child_no_inc = ctx.child(increment_loop_depth=False)
        assert child_no_inc.loop_depth == 1

        child_inc = ctx.child(increment_loop_depth=True)
        assert child_inc.loop_depth == 2


class TestCalleeParamLookup:
    """Tests for callee parameter name lookup."""

    def test_get_callee_param_name_returns_name(self):
        """Test looking up input parameter name from callee context."""
        def mock_lookup(vi_name: str):
            if vi_name == "Helper.vi":
                return {
                    "inputs": [
                        FPTerminalNode(
                            id="in0",
                            kind="input",
                            name="Path Input",
                            is_indicator=False,
                            is_public=True,
                            slot_index=0,
                        ),
                    ],
                    "outputs": [],
                }
            return None

        ctx = CodeGenContext(vi_context_lookup=mock_lookup)

        assert ctx.get_callee_param_name("Helper.vi", 0) == "Path Input"
        assert ctx.get_callee_param_name("Helper.vi", 1) is None
        assert ctx.get_callee_param_name("Unknown.vi", 0) is None

    def test_get_callee_output_name_returns_name(self):
        """Test looking up output parameter name from callee context."""
        def mock_lookup(vi_name: str):
            if vi_name == "Helper.vi":
                return {
                    "inputs": [],
                    "outputs": [
                        FPTerminalNode(
                            id="out0",
                            kind="output",
                            name="Result Path",
                            is_indicator=True,
                            is_public=True,
                            slot_index=0,
                        ),
                    ],
                }
            return None

        ctx = CodeGenContext(vi_context_lookup=mock_lookup)

        assert ctx.get_callee_output_name("Helper.vi", 0) == "Result Path"
        assert ctx.get_callee_output_name("Helper.vi", 1) is None

    def test_callee_lookup_with_polymorphic_variants(self):
        """Test that polymorphic variants are checked when wrapper has no inputs."""
        def mock_lookup(vi_name: str):
            if vi_name == "Poly Wrapper.vi":
                return {
                    "inputs": [],  # No direct inputs
                    "outputs": [],
                    "poly_variants": ["Poly Variant 1.vi"],
                }
            elif vi_name == "Poly Variant 1.vi":
                return {
                    "inputs": [
                        FPTerminalNode(
                            id="v_in0",
                            kind="input",
                            name="Variant Input",
                            is_indicator=False,
                            is_public=True,
                            slot_index=0,
                        ),
                    ],
                    "outputs": [],
                }
            return None

        ctx = CodeGenContext(vi_context_lookup=mock_lookup)

        # Should find the input from the variant
        assert ctx.get_callee_param_name("Poly Wrapper.vi", 0) == "Variant Input"
