"""Tests for condition expression building from LabVIEW dataflow."""

from __future__ import annotations

import ast

from lvkit.codegen.condition_builder import (
    BOOLEAN_PRIMITIVES,
    COMPARISON_PRIMITIVES,
    NOT_PRIMITIVES,
    build_condition_expr,
)
from lvkit.codegen.context import CodeGenContext
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, WireEnd
from lvkit.models import PrimitiveOperation, Terminal


def _make_wired_ctx(
    wires: list[tuple[str, str]],
    bindings: dict[str, str] | None = None,
) -> CodeGenContext:
    """Build a CodeGenContext with proper nodes and edges from wire pairs.

    Each wire pair is (src_terminal_id, dst_terminal_id).
    Creates a PrimitiveNode per terminal so bind/resolve work correctly.
    """
    graph = InMemoryVIGraph()
    added_nodes: dict[str, PrimitiveNode] = {}

    for src_tid, dst_tid in wires:
        for tid in (src_tid, dst_tid):
            if tid not in added_nodes:
                nid = f"node_{tid}"
                node = PrimitiveNode(
                    id=nid,
                    vi="test.vi",
                    name=nid,
                    terminals=[Terminal(id=tid, index=0, direction="output")],
                )
                graph._graph.add_node(nid, node=node)
                graph._term_to_node[tid] = nid
                added_nodes[tid] = node

        src_nid = f"node_{src_tid}"
        dst_nid = f"node_{dst_tid}"
        graph._graph.add_edge(
            src_nid,
            dst_nid,
            source=WireEnd(terminal_id=src_tid, node_id=src_nid),
            dest=WireEnd(terminal_id=dst_tid, node_id=dst_nid),
        )

    ctx = CodeGenContext(graph=graph)
    if bindings:
        for tid, vname in bindings.items():
            ctx.bind(tid, vname)
    return ctx


class TestComparisonPrimitives:
    """Tests for building comparison expressions."""

    def test_build_equal_comparison(self):
        """Test building x == y comparison."""
        ctx = _make_wired_ctx(
            [("src_x", "cmp_in1"), ("src_y", "cmp_in2"), ("cmp_out", "stop_term")],
            {"src_x": "count", "src_y": "max_count"},
        )

        # Equal? primitive (1102)
        cmp_op = PrimitiveOperation(
            id="cmp1",
            name="Equal?",
            labels=["Primitive"],
            primResID=1102,
            terminals=[
                Terminal(id="cmp_in1", index=0, direction="input"),
                Terminal(id="cmp_in2", index=1, direction="input"),
                Terminal(id="cmp_out", index=2, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [cmp_op])

        assert expr is not None
        assert isinstance(expr, ast.Compare)
        assert isinstance(expr.ops[0], ast.Eq)
        code = ast.unparse(expr)
        assert "count" in code
        assert "max_count" in code

    def test_build_greater_or_equal_comparison(self):
        """Test building x >= y comparison."""
        ctx = _make_wired_ctx(
            [("src_x", "cmp_in1"), ("src_y", "cmp_in2"), ("cmp_out", "stop_term")],
            {"src_x": "i", "src_y": "n"},
        )

        # Greater Or Equal? primitive (1103)
        cmp_op = PrimitiveOperation(
            id="cmp1",
            name="Greater Or Equal?",
            labels=["Primitive"],
            primResID=1103,
            terminals=[
                Terminal(id="cmp_in1", index=0, direction="input"),
                Terminal(id="cmp_in2", index=1, direction="input"),
                Terminal(id="cmp_out", index=2, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [cmp_op])

        assert expr is not None
        assert isinstance(expr, ast.Compare)
        assert isinstance(expr.ops[0], ast.GtE)

    def test_build_less_than_comparison(self):
        """Test building x < y comparison."""
        ctx = _make_wired_ctx(
            [("src_x", "cmp_in1"), ("src_y", "cmp_in2"), ("cmp_out", "stop_term")],
            {"src_x": "value", "src_y": "threshold"},
        )

        # Less? primitive (1107)
        cmp_op = PrimitiveOperation(
            id="cmp1",
            name="Less?",
            labels=["Primitive"],
            primResID=1107,
            terminals=[
                Terminal(id="cmp_in1", index=0, direction="input"),
                Terminal(id="cmp_in2", index=1, direction="input"),
                Terminal(id="cmp_out", index=2, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [cmp_op])

        assert expr is not None
        assert isinstance(expr, ast.Compare)
        assert isinstance(expr.ops[0], ast.Lt)


class TestBooleanPrimitives:
    """Tests for building boolean AND/OR expressions."""

    def test_build_and_expression(self):
        """Test building x and y expression."""
        ctx = _make_wired_ctx(
            [("src_a", "and_in1"), ("src_b", "and_in2"), ("and_out", "stop_term")],
            {"src_a": "flag_a", "src_b": "flag_b"},
        )

        # And primitive (1100)
        and_op = PrimitiveOperation(
            id="and1",
            name="And",
            labels=["Primitive"],
            primResID=1100,
            terminals=[
                Terminal(id="and_in1", index=0, direction="input"),
                Terminal(id="and_in2", index=1, direction="input"),
                Terminal(id="and_out", index=2, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [and_op])

        assert expr is not None
        assert isinstance(expr, ast.BoolOp)
        assert isinstance(expr.op, ast.And)

    def test_build_or_expression(self):
        """Test building x or y expression."""
        ctx = _make_wired_ctx(
            [("src_a", "or_in1"), ("src_b", "or_in2"), ("or_out", "stop_term")],
            {"src_a": "done", "src_b": "timeout"},
        )

        # Or primitive (1101)
        or_op = PrimitiveOperation(
            id="or1",
            name="Or",
            labels=["Primitive"],
            primResID=1101,
            terminals=[
                Terminal(id="or_in1", index=0, direction="input"),
                Terminal(id="or_in2", index=1, direction="input"),
                Terminal(id="or_out", index=2, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [or_op])

        assert expr is not None
        assert isinstance(expr, ast.BoolOp)
        assert isinstance(expr.op, ast.Or)


class TestNotPrimitive:
    """Tests for building NOT expressions."""

    def test_build_not_expression(self):
        """Test building not x expression."""
        ctx = _make_wired_ctx(
            [("src_x", "not_in"), ("not_out", "stop_term")],
            {"src_x": "running"},
        )

        # Not primitive (1109)
        not_op = PrimitiveOperation(
            id="not1",
            name="Not",
            labels=["Primitive"],
            primResID=1109,
            terminals=[
                Terminal(id="not_in", index=0, direction="input"),
                Terminal(id="not_out", index=1, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [not_op])

        assert expr is not None
        assert isinstance(expr, ast.UnaryOp)
        assert isinstance(expr.op, ast.Not)


class TestNestedExpressions:
    """Tests for nested/compound expressions."""

    def test_build_nested_or_with_comparisons(self):
        """Test building (a >= b) or (c == d) expression."""
        ctx = _make_wired_ctx(
            [
                ("src_a", "cmp1_in1"),
                ("src_b", "cmp1_in2"),
                ("src_c", "cmp2_in1"),
                ("src_d", "cmp2_in2"),
                ("cmp1_out", "or_in1"),
                ("cmp2_out", "or_in2"),
                ("or_out", "stop_term"),
            ],
            {
                "src_a": "count", "src_b": "max_count",
                "src_c": "status", "src_d": "done_status",
            },
        )

        # Greater Or Equal? (1103)
        cmp1 = PrimitiveOperation(
            id="cmp1",
            name="Greater Or Equal?",
            labels=["Primitive"],
            primResID=1103,
            terminals=[
                Terminal(id="cmp1_in1", index=0, direction="input"),
                Terminal(id="cmp1_in2", index=1, direction="input"),
                Terminal(id="cmp1_out", index=2, direction="output"),
            ],
        )

        # Equal? (1102)
        cmp2 = PrimitiveOperation(
            id="cmp2",
            name="Equal?",
            labels=["Primitive"],
            primResID=1102,
            terminals=[
                Terminal(id="cmp2_in1", index=0, direction="input"),
                Terminal(id="cmp2_in2", index=1, direction="input"),
                Terminal(id="cmp2_out", index=2, direction="output"),
            ],
        )

        # Or (1101)
        or_op = PrimitiveOperation(
            id="or1",
            name="Or",
            labels=["Primitive"],
            primResID=1101,
            terminals=[
                Terminal(id="or_in1", index=0, direction="input"),
                Terminal(id="or_in2", index=1, direction="input"),
                Terminal(id="or_out", index=2, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [cmp1, cmp2, or_op])

        assert expr is not None
        assert isinstance(expr, ast.BoolOp)
        assert isinstance(expr.op, ast.Or)
        # Should have two comparison sub-expressions
        assert len(expr.values) == 2

    def test_build_not_of_comparison(self):
        """Test building not (x < y) expression."""
        ctx = _make_wired_ctx(
            [
                ("src_x", "cmp_in1"),
                ("src_y", "cmp_in2"),
                ("cmp_out", "not_in"),
                ("not_out", "stop_term"),
            ],
            {"src_x": "value", "src_y": "limit"},
        )

        # Less? (1107)
        cmp_op = PrimitiveOperation(
            id="cmp1",
            name="Less?",
            labels=["Primitive"],
            primResID=1107,
            terminals=[
                Terminal(id="cmp_in1", index=0, direction="input"),
                Terminal(id="cmp_in2", index=1, direction="input"),
                Terminal(id="cmp_out", index=2, direction="output"),
            ],
        )

        # Not (1109)
        not_op = PrimitiveOperation(
            id="not1",
            name="Not",
            labels=["Primitive"],
            primResID=1109,
            terminals=[
                Terminal(id="not_in", index=0, direction="input"),
                Terminal(id="not_out", index=1, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [cmp_op, not_op])

        assert expr is not None
        assert isinstance(expr, ast.UnaryOp)
        assert isinstance(expr.op, ast.Not)
        # The operand should be the comparison
        assert isinstance(expr.operand, ast.Compare)


class TestCpdArithConditions:
    """Tests for cpdArith (compound arithmetic) in conditions."""

    def test_build_cpd_arith_or(self):
        """Test building condition from cpdArith OR node."""
        ctx = _make_wired_ctx(
            [("src_a", "cpd_in1"), ("src_b", "cpd_in2"), ("cpd_out", "stop_term")],
            {"src_a": "flag_1", "src_b": "flag_2"},
        )

        # cpdArith with OR operation (no primResID)
        cpd_op = PrimitiveOperation(
            id="cpd1",
            name="Compound Or",
            labels=["Compound"],
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="cpd_in1", index=1, direction="input"),
                Terminal(id="cpd_in2", index=2, direction="input"),
                Terminal(id="cpd_out", index=0, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [cpd_op])

        assert expr is not None
        assert isinstance(expr, ast.BoolOp)
        assert isinstance(expr.op, ast.Or)

    def test_build_cpd_arith_and(self):
        """Test building condition from cpdArith AND node."""
        ctx = _make_wired_ctx(
            [("src_a", "cpd_in1"), ("src_b", "cpd_in2"), ("cpd_out", "stop_term")],
            {"src_a": "condition_1", "src_b": "condition_2"},
        )

        cpd_op = PrimitiveOperation(
            id="cpd1",
            name="Compound And",
            labels=["Compound"],
            node_type="cpdArith",
            operation="and",
            terminals=[
                Terminal(id="cpd_in1", index=1, direction="input"),
                Terminal(id="cpd_in2", index=2, direction="input"),
                Terminal(id="cpd_out", index=0, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [cpd_op])

        assert expr is not None
        assert isinstance(expr, ast.BoolOp)
        assert isinstance(expr.op, ast.And)


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_returns_none_for_unknown_terminal(self):
        """Test that unknown stop terminal returns None."""
        ctx = CodeGenContext()
        expr = build_condition_expr("nonexistent", ctx, [])
        assert expr is None

    def test_returns_none_for_no_source_operation(self):
        """Test returns None when stop terminal has no source operation."""
        ctx = _make_wired_ctx(
            [("src1", "stop_term")],
            {"src1": "some_value"},
        )

        # No operations provided that output to src1
        expr = build_condition_expr("stop_term", ctx, [])
        assert expr is None

    def test_returns_none_for_unknown_primitive(self):
        """Test returns None for unknown primitive ID."""
        ctx = _make_wired_ctx(
            [("prim_out", "stop_term")],
        )

        # Unknown primitive ID
        unknown_op = PrimitiveOperation(
            id="unk1",
            name="Unknown",
            labels=["Primitive"],
            primResID=99999,
            terminals=[
                Terminal(id="prim_out", index=0, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [unknown_op])
        assert expr is None

    def test_returns_none_for_insufficient_inputs(self):
        """Test returns None when comparison has insufficient inputs."""
        # Only one input wired; cmp_in2 is not connected
        ctx = _make_wired_ctx(
            [("src_x", "cmp_in1"), ("cmp_out", "stop_term")],
            {"src_x": "value"},
        )

        # Equal? primitive needs 2 inputs
        cmp_op = PrimitiveOperation(
            id="cmp1",
            name="Equal?",
            labels=["Primitive"],
            primResID=1102,
            terminals=[
                Terminal(id="cmp_in1", index=0, direction="input"),
                Terminal(id="cmp_in2", index=1, direction="input"),
                Terminal(id="cmp_out", index=2, direction="output"),
            ],
        )

        expr = build_condition_expr("stop_term", ctx, [cmp_op])
        assert expr is None


class TestPrimitiveMappings:
    """Tests to verify the primitive ID mappings are correct."""

    def test_comparison_primitives_mapping(self):
        """Verify comparison primitive IDs map to correct AST operators."""
        assert COMPARISON_PRIMITIVES[1102] == ast.Eq
        assert COMPARISON_PRIMITIVES[1103] == ast.GtE
        assert COMPARISON_PRIMITIVES[1105] == ast.NotEq
        assert COMPARISON_PRIMITIVES[1107] == ast.Lt
        assert COMPARISON_PRIMITIVES[1108] == ast.LtE
        assert COMPARISON_PRIMITIVES[1110] == ast.Gt

    def test_boolean_primitives_mapping(self):
        """Verify boolean primitive IDs map to correct AST operators."""
        assert BOOLEAN_PRIMITIVES[1100] == ast.And
        assert BOOLEAN_PRIMITIVES[1101] == ast.Or

    def test_not_primitives_set(self):
        """Verify NOT primitive ID is in the set."""
        assert 1109 in NOT_PRIMITIVES
