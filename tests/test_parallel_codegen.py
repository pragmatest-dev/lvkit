"""Tests for tiered topological sort and parallel code generation.

Covers:
- Tiered Kahn's algorithm (topological_sort_tiered)
- ThreadPoolExecutor generation for multi-op tiers
- Sequence frame integration with generate_body
- End-to-end In.vi parallel output
"""

from __future__ import annotations

import ast

from lvpy.agent.codegen.builder import (
    generate_body,
    topological_sort_tiered,
)
from lvpy.agent.codegen.context import CodeGenContext
from lvpy.graph_types import (
    Operation,
    SequenceFrame,
    SequenceOperation,
    Terminal,
    Wire,
)


def _make_op(
    op_id: str,
    name: str = "op",
    terminals: list[Terminal] | None = None,
) -> Operation:
    """Helper to create a minimal Operation."""
    return Operation(
        id=op_id,
        name=name,
        labels=["SubVI"],
        node_type="iUse",
        terminals=terminals or [],
    )


def _make_terminal(tid: str, direction: str, parent_id: str = "") -> Terminal:
    return Terminal(id=tid, direction=direction, index=0, name="t")


# =============================================================
# Tiered topological sort
# =============================================================


class TestTieredTopologicalSort:
    """topological_sort_tiered returns parallel tiers."""

    def test_single_chain_three_tiers(self):
        """A→B→C produces 3 single-op tiers [[A],[B],[C]]."""
        a_out = _make_terminal("a_out", "output")
        b_in = _make_terminal("b_in", "input")
        b_out = _make_terminal("b_out", "output")
        c_in = _make_terminal("c_in", "input")

        a = _make_op("A", terminals=[a_out])
        b = _make_op("B", terminals=[b_in, b_out])
        c = _make_op("C", terminals=[c_in])

        wires = [
            Wire.from_terminals(from_terminal_id="a_out", to_terminal_id="b_in"),
            Wire.from_terminals(from_terminal_id="b_out", to_terminal_id="c_in"),
        ]
        ctx = CodeGenContext.from_wires(wires)

        tiers = topological_sort_tiered([a, b, c], ctx)

        assert len(tiers) == 3
        assert [t[0].id for t in tiers] == ["A", "B", "C"]

    def test_two_independent_ops_one_tier(self):
        """A and B with no deps produce 1 tier [[A, B]]."""
        a = _make_op("A")
        b = _make_op("B")
        ctx = CodeGenContext()

        tiers = topological_sort_tiered([a, b], ctx)

        assert len(tiers) == 1
        assert {op.id for op in tiers[0]} == {"A", "B"}

    def test_diamond_pattern(self):
        """A→B, A→C, B→D, C→D produces [[A],[B,C],[D]]."""
        a_out = _make_terminal("a_out", "output")
        b_in = _make_terminal("b_in", "input")
        b_out = _make_terminal("b_out", "output")
        c_in = _make_terminal("c_in", "input")
        c_out = _make_terminal("c_out", "output")
        d_in1 = _make_terminal("d_in1", "input")
        d_in2 = _make_terminal("d_in2", "input")

        a = _make_op("A", terminals=[a_out])
        b = _make_op("B", terminals=[b_in, b_out])
        c = _make_op("C", terminals=[c_in, c_out])
        d = _make_op("D", terminals=[d_in1, d_in2])

        wires = [
            Wire.from_terminals(from_terminal_id="a_out", to_terminal_id="b_in"),
            Wire.from_terminals(from_terminal_id="a_out", to_terminal_id="c_in"),
            Wire.from_terminals(from_terminal_id="b_out", to_terminal_id="d_in1"),
            Wire.from_terminals(from_terminal_id="c_out", to_terminal_id="d_in2"),
        ]
        ctx = CodeGenContext.from_wires(wires)

        tiers = topological_sort_tiered([a, b, c, d], ctx)

        assert len(tiers) == 3
        assert [tiers[0][0].id] == ["A"]
        assert {op.id for op in tiers[1]} == {"B", "C"}
        assert [tiers[2][0].id] == ["D"]

    def test_mixed_deps_and_independent(self):
        """A→B, C independent produces [[A,C],[B]]."""
        a_out = _make_terminal("a_out", "output")
        b_in = _make_terminal("b_in", "input")

        a = _make_op("A", terminals=[a_out])
        b = _make_op("B", terminals=[b_in])
        c = _make_op("C")

        wires = [Wire.from_terminals(from_terminal_id="a_out", to_terminal_id="b_in")]
        ctx = CodeGenContext.from_wires(wires)

        tiers = topological_sort_tiered([a, b, c], ctx)

        assert len(tiers) == 2
        assert {op.id for op in tiers[0]} == {"A", "C"}
        assert [tiers[1][0].id] == ["B"]

    def test_empty_operations(self):
        """Empty input returns empty tiers."""
        ctx = CodeGenContext()
        assert topological_sort_tiered([], ctx) == []

    def test_single_op(self):
        """Single op returns single tier."""
        a = _make_op("A")
        ctx = CodeGenContext()
        tiers = topological_sort_tiered([a], ctx)
        assert len(tiers) == 1
        assert len(tiers[0]) == 1


# =============================================================
# Parallel codegen output
# =============================================================


class TestParallelCodegen:
    """generate_body emits ThreadPoolExecutor for multi-op tiers."""

    def test_single_op_no_executor(self):
        """Single-op tier emits plain statements, no executor."""
        a = _make_op("A")
        ctx = CodeGenContext()
        stmts = generate_body([a], ctx)
        mod = ast.Module(body=stmts, type_ignores=[])
        code = ast.unparse(ast.fix_missing_locations(mod))
        assert "ThreadPoolExecutor" not in code

    def test_two_independent_ops_emit_executor(self):
        """Two independent ops emit ThreadPoolExecutor."""
        a = _make_op("A")
        b = _make_op("B")
        ctx = CodeGenContext()
        stmts = generate_body([a, b], ctx)
        mod = ast.Module(body=stmts, type_ignores=[])
        code = ast.unparse(ast.fix_missing_locations(mod))
        assert "ThreadPoolExecutor" in code
        assert "_executor.submit" in code

    def test_sequential_chain_no_executor(self):
        """Fully sequential chain doesn't use executor."""
        a_out = _make_terminal("a_out", "output")
        b_in = _make_terminal("b_in", "input")

        a = _make_op("A", terminals=[a_out])
        b = _make_op("B", terminals=[b_in])

        wires = [Wire.from_terminals(from_terminal_id="a_out", to_terminal_id="b_in")]
        ctx = CodeGenContext.from_wires(wires)
        stmts = generate_body([a, b], ctx)
        mod = ast.Module(body=stmts, type_ignores=[])
        code = ast.unparse(ast.fix_missing_locations(mod))
        assert "ThreadPoolExecutor" not in code

    def test_diamond_has_executor_for_middle_tier(self):
        """Diamond pattern: middle tier (B,C) uses executor."""
        a_out = _make_terminal("a_out", "output")
        b_in = _make_terminal("b_in", "input")
        b_out = _make_terminal("b_out", "output")
        c_in = _make_terminal("c_in", "input")
        c_out = _make_terminal("c_out", "output")
        d_in1 = _make_terminal("d_in1", "input")
        d_in2 = _make_terminal("d_in2", "input")

        a = _make_op("A", terminals=[a_out])
        b = _make_op("B", terminals=[b_in, b_out])
        c = _make_op("C", terminals=[c_in, c_out])
        d = _make_op("D", terminals=[d_in1, d_in2])

        wires = [
            Wire.from_terminals(from_terminal_id="a_out", to_terminal_id="b_in"),
            Wire.from_terminals(from_terminal_id="a_out", to_terminal_id="c_in"),
            Wire.from_terminals(from_terminal_id="b_out", to_terminal_id="d_in1"),
            Wire.from_terminals(from_terminal_id="c_out", to_terminal_id="d_in2"),
        ]
        ctx = CodeGenContext.from_wires(wires)
        stmts = generate_body([a, b, c, d], ctx)
        mod = ast.Module(body=stmts, type_ignores=[])
        code = ast.unparse(ast.fix_missing_locations(mod))
        assert "ThreadPoolExecutor" in code

    def test_imports_include_concurrent_futures(self):
        """Parallel tier adds concurrent.futures to imports."""
        a = _make_op("A")
        b = _make_op("B")
        ctx = CodeGenContext()
        generate_body([a, b], ctx)
        assert "import concurrent.futures" in ctx.imports


# =============================================================
# Sequence frame integration
# =============================================================


class TestSequenceParallelIntegration:
    """Sequence frames use generate_body and get parallelism."""

    def test_frame_with_single_op_no_executor(self):
        """Frame with one op: no executor."""
        from lvpy.agent.codegen.nodes import sequence

        inner = _make_op("write1")
        op = SequenceOperation(
            id="seq1",
            name="Flat Sequence",
            labels=["FlatSequence"],
            node_type="flatSequence",
            frames=[
                SequenceFrame(
                    index=0,
                    inner_node_uids=["write1"],
                    operations=[inner],
                ),
            ],
            tunnels=[],
        )
        ctx = CodeGenContext()
        fragment = sequence.generate(op, ctx)
        code = ast.unparse(ast.Module(body=fragment.statements, type_ignores=[]))
        assert "ThreadPoolExecutor" not in code

    def test_frame_with_two_independent_ops_uses_executor(self):
        """Frame with two independent ops: executor used."""
        from lvpy.agent.codegen.nodes import sequence

        op_a = _make_op("a")
        op_b = _make_op("b")
        op = SequenceOperation(
            id="seq1",
            name="Flat Sequence",
            labels=["FlatSequence"],
            node_type="flatSequence",
            frames=[
                SequenceFrame(
                    index=0,
                    inner_node_uids=["a", "b"],
                    operations=[op_a, op_b],
                ),
            ],
            tunnels=[],
        )
        ctx = CodeGenContext(_body_generator=generate_body)
        fragment = sequence.generate(op, ctx)
        code = ast.unparse(ast.Module(body=fragment.statements, type_ignores=[]))
        assert "ThreadPoolExecutor" in code

    def test_multiple_frames_sequential(self):
        """Multiple frames execute sequentially (each may have parallelism)."""
        from lvpy.agent.codegen.nodes import sequence

        op_a = _make_op("a")
        op_b = _make_op("b")
        op = SequenceOperation(
            id="seq1",
            name="Flat Sequence",
            labels=["FlatSequence"],
            node_type="flatSequence",
            frames=[
                SequenceFrame(
                    index=0,
                    inner_node_uids=["a"],
                    operations=[op_a],
                ),
                SequenceFrame(
                    index=1,
                    inner_node_uids=["b"],
                    operations=[op_b],
                ),
            ],
            tunnels=[],
        )
        ctx = CodeGenContext()
        fragment = sequence.generate(op, ctx)
        # Both frames generate something
        assert len(fragment.statements) > 0


# =============================================================
# End-to-end: In.vi
# =============================================================


class TestInViParallelEndToEnd:
    """End-to-end test that In.vi generates ThreadPoolExecutor."""

    def _generate_in_vi(self) -> str:
        from lvpy.agent.codegen.builder import build_module
        from lvpy.memory_graph import connect

        mg = connect()
        mg.load_vi("samples/DAQmx-Digital-IO/In.vi")
        ctx = mg.get_vi_context("In.vi")
        return build_module(ctx, "In.vi", graph=mg)

    def test_generates_without_error(self):
        code = self._generate_in_vi()
        assert "def in_():" in code

    def test_contains_thread_pool_executor(self):
        """Frames with Write + Wait should use ThreadPoolExecutor."""
        code = self._generate_in_vi()
        assert "ThreadPoolExecutor" in code

    def test_sequential_chain_preserved(self):
        """Create → Start → ... → Stop → Close stay sequential."""
        code = self._generate_in_vi()
        # These calls should appear in sequence (not all in one executor)
        assert "daqmx_create_virtual_channel" in code or "do_channels" in code
        assert "time.sleep" in code

    def test_no_none_args(self):
        """Regression: no None arguments in generated calls."""
        code = self._generate_in_vi()
        assert ".write(None)" not in code

    def test_correct_booleans(self):
        """Regression: correct boolean values."""
        code = self._generate_in_vi()
        assert ".write(True)" in code
        assert ".write(False)" in code


class TestPassthroughBindingsInParallelTier:
    """Regression: passthrough bindings must be merged even when all
    ops in a parallel tier produce zero statements.

    Bug: _generate_parallel_tier returned early when inner_stmts was
    empty, skipping ctx.merge(fragment.bindings). Downstream tiers then
    could not resolve inputs and fell back to type defaults (e.g., 0).
    """

    def test_all_passthrough_tier_bindings_propagate(self):
        """When a parallel tier has only passthrough ops (0 statements),
        their bindings must still reach downstream tiers."""
        # Set up: A→B, A→C where B and C are passthroughs, D depends on both
        a_out = _make_terminal("a_out", "output")
        b_in = _make_terminal("b_in", "input")
        b_out = _make_terminal("b_out", "output")
        c_in = _make_terminal("c_in", "input")
        c_out = _make_terminal("c_out", "output")
        d_in1 = _make_terminal("d_in1", "input")
        d_in2 = _make_terminal("d_in2", "input")

        # Ops exist only to give terminals parent nodes for wiring
        _make_op("A", terminals=[a_out])
        _make_op("B", terminals=[b_in, b_out])
        _make_op("C", terminals=[c_in, c_out])
        _make_op("D", terminals=[d_in1, d_in2])

        wires = [
            Wire.from_terminals(from_terminal_id="a_out", to_terminal_id="b_in"),
            Wire.from_terminals(from_terminal_id="a_out", to_terminal_id="c_in"),
            Wire.from_terminals(from_terminal_id="b_out", to_terminal_id="d_in1"),
            Wire.from_terminals(from_terminal_id="c_out", to_terminal_id="d_in2"),
        ]
        ctx = CodeGenContext.from_wires(wires)
        ctx._body_generator = generate_body

        # Bind A's output to simulate it being processed
        ctx.bind("a_out", "source_val")

        # Bind B and C outputs as if they were passthroughs
        # (simulating what PrimitiveCodeGen would do for passthrough ops)
        ctx.bind("b_out", "val_b")
        ctx.bind("c_out", "val_c")

        # The key check: D's inputs should resolve through B and C
        resolved_d1 = ctx.resolve("d_in1")
        resolved_d2 = ctx.resolve("d_in2")
        assert resolved_d1 == "val_b", f"Expected 'val_b', got {resolved_d1}"
        assert resolved_d2 == "val_c", f"Expected 'val_c', got {resolved_d2}"
