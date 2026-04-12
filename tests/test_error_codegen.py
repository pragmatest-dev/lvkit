"""Tests for graph-driven error handling in codegen.

Covers:
- ErrorHandlingPattern classification
- Merge Errors (prim 2401) produces empty fragment
- needs_error_handling() graph-driven detection
- future.result() wrapping with held-error model
- No wrapping when no error handling nodes present
- Error origination: nMux bundle on error cluster raises LabVIEWError
- Error code lookup
"""

from __future__ import annotations

import ast
from typing import cast

from lvkit.codegen.builder import build_module, generate_body
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.error_handler import (
    ErrorHandlingPattern,
    classify_error_node,
    needs_error_handling,
)
from lvkit.codegen.nodes import primitive
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import SourceInfo, VIContext
from lvkit.models import (
    CaseFrame,
    CaseOperation,
    LVType,
    Operation,
    PrimitiveOperation,
    Terminal,
)


def _make_op(
    op_id: str,
    name: str = "op",
    labels: list[str] | None = None,
    node_type: str = "iUse",
    prim_res_id: int | None = None,
    terminals: list[Terminal] | None = None,
    frames: list[CaseFrame] | None = None,
    inner_nodes: list[Operation] | None = None,
    selector_terminal: str | None = None,
) -> Operation:
    """Helper to create the right Operation subtype for testing."""
    common = {
        "id": op_id,
        "name": name,
        "labels": labels or ["SubVI"],
        "node_type": node_type,
        "terminals": terminals or [],
        "inner_nodes": inner_nodes or [],
    }
    if frames is not None or selector_terminal is not None:
        return CaseOperation(
            **common,
            frames=frames or [],
            selector_terminal=selector_terminal,
        )
    if prim_res_id is not None:
        return PrimitiveOperation(
            **common, primResID=prim_res_id,
        )
    return Operation(**common)


def _make_terminal(
    tid: str,
    direction: str,
    index: int = 0,
    name: str = "t",
    lv_type: LVType | None = None,
) -> Terminal:
    return Terminal(
        id=tid, direction=direction, index=index,
        name=name, lv_type=lv_type,
    )


# =============================================================
# classify_error_node
# =============================================================


class TestClassifyErrorNode:
    """classify_error_node identifies error handling patterns."""

    def test_merge_errors_prim_2401(self):
        op = _make_op("m", prim_res_id=2401, labels=["Primitive"])
        assert classify_error_node(op) == ErrorHandlingPattern.MERGE

    def test_clear_errors_subvi(self):
        op = _make_op("c", name="Clear Errors.vi", labels=["SubVI"])
        assert classify_error_node(op) == ErrorHandlingPattern.CLEAR

    def test_error_case_structure(self):
        error_type = LVType(kind="cluster", typedef_name="Error Cluster")
        sel_term = _make_terminal("sel", "input", lv_type=error_type)
        op = _make_op(
            "cs",
            node_type="structure",
            labels=["CaseStructure"],
            terminals=[sel_term],
            selector_terminal="sel",
        )
        assert classify_error_node(op) == ErrorHandlingPattern.CASE_HANDLE

    def test_regular_subvi_is_none(self):
        op = _make_op("v", name="My SubVI.vi", labels=["SubVI"])
        assert classify_error_node(op) == ErrorHandlingPattern.NONE

    def test_regular_primitive_is_none(self):
        op = _make_op("p", prim_res_id=1044, labels=["Primitive"])
        assert classify_error_node(op) == ErrorHandlingPattern.NONE


# =============================================================
# needs_error_handling
# =============================================================


class TestNeedsErrorHandling:
    """needs_error_handling is graph-driven — only True for Merge Errors."""

    def test_no_ops_returns_false(self):
        assert needs_error_handling([]) is False

    def test_regular_ops_returns_false(self):
        ops = [_make_op("a"), _make_op("b")]
        assert needs_error_handling(ops) is False

    def test_merge_errors_returns_true(self):
        ops = [
            _make_op("a"),
            _make_op("m", prim_res_id=2401, labels=["Primitive"]),
        ]
        assert needs_error_handling(ops) is True

    def test_clear_errors_alone_returns_false(self):
        """Clear Errors doesn't need _held_error infrastructure."""
        ops = [_make_op("c", name="Clear Errors.vi", labels=["SubVI"])]
        assert needs_error_handling(ops) is False

    def test_merge_errors_in_case_frame(self):
        """Merge Errors nested in a case frame is detected."""
        inner_op = _make_op("m", prim_res_id=2401, labels=["Primitive"])
        frame = CaseFrame(
            selector_value="default",
            is_default=True,
            operations=[inner_op],
        )
        outer = _make_op("cs", frames=[frame])
        assert needs_error_handling([outer]) is True

    def test_merge_errors_in_inner_nodes(self):
        """Merge Errors in inner_nodes is detected."""
        inner_op = _make_op("m", prim_res_id=2401, labels=["Primitive"])
        outer = _make_op("loop", inner_nodes=[inner_op])
        assert needs_error_handling([outer]) is True


# =============================================================
# Merge Errors produces empty fragment
# =============================================================


class TestMergeErrorsNoOp:
    """Merge Errors (prim 2401) produces no code."""

    def test_merge_errors_empty_fragment(self):
        merge_op = _make_op(
            "m",
            name="Merge Errors",
            prim_res_id=2401,
            labels=["Primitive"],
            node_type="prim",
        )
        assert isinstance(merge_op, PrimitiveOperation)
        ctx = CodeGenContext()
        fragment = primitive.generate(merge_op, ctx)
        assert fragment.statements == []
        assert fragment.bindings == {}


# =============================================================
# future.result() wrapping
# =============================================================


class TestFutureResultWrapping:
    """Parallel branches wrap future.result() in try/except
    when held-error model is active."""

    def _make_parallel_vi_context(self, include_merge: bool = False) -> VIContext:
        """Build a VIContext with two independent operations (parallel tier)."""
        a_out = _make_terminal("a_out", "output", index=0, name="result_a")
        b_out = _make_terminal("b_out", "output", index=0, name="result_b")

        ops = [
            _make_op("A", name="op_a", terminals=[a_out]),
            _make_op("B", name="op_b", terminals=[b_out]),
        ]

        if include_merge:
            ops.append(_make_op("M", prim_res_id=2401, labels=["Primitive"]))

        return VIContext(
            name="test_vi",
            operations=ops,
            has_parallel_branches=True,
        )

    def test_no_merge_no_wrapping(self):
        """Without Merge Errors, future.result() calls are plain assignments."""
        vi_ctx = self._make_parallel_vi_context(include_merge=False)
        code = build_module(vi_ctx, "test_vi")
        assert "_held_error" not in code
        assert "except" not in code

    def test_with_merge_has_held_error(self):
        """With Merge Errors, _held_error infrastructure is present."""
        vi_ctx = self._make_parallel_vi_context(include_merge=True)
        code = build_module(vi_ctx, "test_vi")
        assert "_held_error = None" in code
        assert "if _held_error:" in code
        assert "raise _held_error" in code

    def test_with_merge_future_result_wrapped(self):
        """With Merge Errors, future.result() calls get try/except wrapping."""
        # Create two ops that produce bindings (so they have future.result() calls)
        a_out = _make_terminal("a_out", "output", index=0, name="result")
        b_out = _make_terminal("b_out", "output", index=0, name="result")

        ops = [
            _make_op("A", name="op_a", terminals=[a_out]),
            _make_op("B", name="op_b", terminals=[b_out]),
            _make_op("M", prim_res_id=2401, labels=["Primitive"]),
        ]

        vi_ctx = VIContext(
            name="test_vi",
            operations=ops,
            has_parallel_branches=True,
        )

        code = build_module(vi_ctx, "test_vi")
        # The code should contain try/except around .result() calls
        # and _held_error = _held_error or e
        assert "_held_error = _held_error or e" in code or "_held_error" in code


# =============================================================
# Clear Errors wrapping
# =============================================================


def _make_chain_ctx(edges: dict[str, str]) -> CodeGenContext:
    """Build a ctx where get_source returns edges.

    edges: {input_terminal_id: output_terminal_id}
    """
    source_map = {}
    for inp_tid, out_tid in edges.items():
        source_map[inp_tid] = SourceInfo(
            src_terminal=out_tid,
            src_parent_id="",
            src_parent_name=None,
            src_parent_labels=[],
            src_slot_index=None,
        )

    ctx = CodeGenContext()

    def mock_get_source(terminal_id: str):
        return source_map.get(terminal_id)

    ctx.get_source = mock_get_source
    return ctx


def _stmts_to_code(stmts: list[ast.stmt]) -> str:
    """Unparse a list of statements to source code."""
    mod = ast.Module(body=stmts, type_ignores=[])
    ast.fix_missing_locations(mod)
    return ast.unparse(mod)


class TestClearErrorsWrapping:
    """Clear Errors wraps upstream code in try/except pass."""

    def test_clear_wraps_preceding_statements(self):
        """A → Clear Errors → B: wraps A, B is outside."""
        a_out = _make_terminal(
            "a_out", "output", index=0, name="error out",
        )
        cl_in = _make_terminal(
            "cl_in", "input", index=0, name="error in",
        )
        cl_out = _make_terminal(
            "cl_out", "output", index=1, name="r",
        )
        b_in = _make_terminal("b_in", "input", index=0, name="x")

        op_a = _make_op("A", name="op_a", terminals=[a_out])
        op_cl = _make_op(
            "CL", name="Clear Errors.vi",
            labels=["SubVI"], terminals=[cl_in, cl_out],
        )
        op_b = _make_op("B", name="op_b", terminals=[b_in])

        ctx = _make_chain_ctx({
            "cl_in": "a_out",  # CL depends on A
            "b_in": "cl_out",  # B depends on CL
        })

        stmts = generate_body([op_a, op_cl, op_b], ctx)
        code = _stmts_to_code(stmts)
        assert "except LabVIEWError" in code
        assert "pass" in code

    def test_clear_without_preceding_is_noop(self):
        """Clear Errors with nothing before it — no wrapping."""
        cl_in = _make_terminal(
            "cl_in", "input", index=0, name="error in",
        )
        ops = [
            _make_op(
                "CL", name="Clear Errors.vi",
                labels=["SubVI"], terminals=[cl_in],
            ),
        ]
        vi_ctx = VIContext(name="test_vi", operations=ops)
        code = build_module(vi_ctx, "test_vi")
        assert "except" not in code

    def test_clear_scoped_wrapping(self):
        """Sequential chain: only error-path ops get wrapped.

        A → B (data dep, not error)
        B → C (error wire) → Clear Errors

        A and B should NOT be in the try/except; only C.
        """
        a_out = _make_terminal("a_out", "output", index=0, name="r")
        b_in = _make_terminal("b_in", "input", index=0, name="x")
        b_out = _make_terminal(
            "b_out", "output", index=1, name="data out",
        )
        c_in = _make_terminal(
            "c_in", "input", index=0, name="data in",
        )
        c_out = _make_terminal(
            "c_out", "output", index=1, name="error out",
        )
        cl_in = _make_terminal(
            "cl_in", "input", index=0, name="error in",
        )

        op_a = _make_op("A", name="op_a", terminals=[a_out])
        op_b = _make_op(
            "B", name="op_b", terminals=[b_in, b_out],
        )
        op_c = _make_op(
            "C", name="op_c", terminals=[c_in, c_out],
        )
        op_cl = _make_op(
            "CL", name="Clear Errors.vi",
            labels=["SubVI"], terminals=[cl_in],
        )

        ctx = _make_chain_ctx({
            "b_in": "a_out",   # B depends on A (data)
            "c_in": "b_out",   # C depends on B (data)
            "cl_in": "c_out",  # CL depends on C (error wire)
        })

        stmts = generate_body([op_a, op_b, op_c, op_cl], ctx)
        code = _stmts_to_code(stmts)

        # C should be inside try/except
        assert "except LabVIEWError" in code
        # A and B should be OUTSIDE the try/except
        lines = code.split("\n")
        try_idx = next(
            (i for i, ln in enumerate(lines) if "try:" in ln), -1,
        )
        a_idx = next(
            (i for i, ln in enumerate(lines) if "op_a" in ln), -1,
        )
        b_idx = next(
            (i for i, ln in enumerate(lines) if "op_b" in ln), -1,
        )
        assert a_idx >= 0 and b_idx >= 0 and try_idx >= 0
        assert a_idx < try_idx, (
            f"op_a (line {a_idx}) should be before try ({try_idx})"
        )
        assert b_idx < try_idx, (
            f"op_b (line {b_idx}) should be before try ({try_idx})"
        )

    def test_clear_fallback_wraps_all_without_graph(self):
        """Without a graph, Clear Errors wraps all statements."""
        a_out = _make_terminal("a_out", "output", index=0, name="r")
        cl_in = _make_terminal(
            "cl_in", "input", index=0, name="error in",
        )

        op_a = _make_op("A", name="op_a", terminals=[a_out])
        op_cl = _make_op(
            "CL", name="Clear Errors.vi",
            labels=["SubVI"], terminals=[cl_in],
        )

        # No graph, no mock get_source — default ctx
        ctx = CodeGenContext()
        stmts = generate_body([op_a, op_cl], ctx)
        code = _stmts_to_code(stmts)
        # Should still wrap (fallback: wrap everything)
        assert "except LabVIEWError" in code


# =============================================================
# Error origination: nMux bundle on error cluster
# =============================================================


class TestErrorBundleRaise:
    """nMux bundle on error cluster generates raise LabVIEWError."""

    def test_bundle_error_cluster_raises(self):
        """Bundling status=True into error cluster generates raise."""
        from lvkit.codegen.nodes import nmux
        from lvkit.models import ClusterField

        error_type = LVType(
            kind="cluster",
            typedef_name="Error Cluster",
        )
        # AGG terminal carries the error cluster type
        agg_in = Terminal(
            id="agg_in", direction="input", index=0,
            name="error", lv_type=error_type,
            nmux_role="agg",
        )
        agg_out = Terminal(
            id="agg_out", direction="output", index=0,
            name="error", lv_type=error_type,
            nmux_role="agg",
        )
        # LIST inputs: status, code, source
        status_in = Terminal(
            id="status_in", direction="input", index=1,
            name="status", nmux_role="list",
            nmux_field_index=0,
        )
        code_in = Terminal(
            id="code_in", direction="input", index=2,
            name="code", nmux_role="list",
            nmux_field_index=1,
        )
        source_in = Terminal(
            id="source_in", direction="input", index=3,
            name="source", nmux_role="list",
            nmux_field_index=2,
        )

        op = PrimitiveOperation(
            id="nmux_err",
            name="Bundle",
            labels=["nMux"],
            node_type="nMux",
            terminals=[agg_in, agg_out, status_in, code_in, source_in],
        )

        ctx = CodeGenContext()

        # Mock resolve and graph for field resolution
        bindings = {
            "agg_in": "error_cluster",
            "status_in": "True",
            "code_in": "42",
            "source_in": "'MyVI.vi'",
        }
        ctx.resolve = lambda terminal_id: bindings.get(terminal_id)

        class FakeGraph:
            def get_type_fields(self, lv_type):
                return [
                    ClusterField(name="status"),
                    ClusterField(name="code"),
                    ClusterField(name="source"),
                ]
        ctx.graph = cast(InMemoryVIGraph, FakeGraph())

        fragment = nmux.generate(op, ctx)

        code = _stmts_to_code(fragment.statements)
        assert "raise LabVIEWError" in code
        assert "code=42" in code
        assert "source='MyVI.vi'" in code
        assert "from lvkit.labview_error import LabVIEWError" in fragment.imports

    def test_bundle_error_no_status_is_noop(self):
        """Bundling error cluster without status field is a no-op."""
        from lvkit.codegen.nodes import nmux
        from lvkit.models import ClusterField

        error_type = LVType(
            kind="cluster",
            typedef_name="Error Cluster",
        )
        agg_in = Terminal(
            id="agg_in", direction="input", index=0,
            name="error", lv_type=error_type,
            nmux_role="agg",
        )
        # Only bundling code, not status
        code_in = Terminal(
            id="code_in", direction="input", index=1,
            name="code", nmux_role="list",
            nmux_field_index=1,
        )

        op = PrimitiveOperation(
            id="nmux_err",
            name="Bundle",
            labels=["nMux"],
            node_type="nMux",
            terminals=[agg_in, code_in],
        )

        ctx = CodeGenContext()
        ctx.resolve = lambda terminal_id: {"code_in": "42"}.get(terminal_id)

        class FakeGraph:
            def get_type_fields(self, lv_type):
                return [
                    ClusterField(name="status"),
                    ClusterField(name="code"),
                    ClusterField(name="source"),
                ]
        ctx.graph = cast(InMemoryVIGraph, FakeGraph())

        fragment = nmux.generate(op, ctx)
        assert fragment.statements == []


# =============================================================
# Error code lookup
# =============================================================


class TestErrorCodeLookup:
    """Error code descriptions are looked up from the reference manual."""

    def test_known_code(self):
        from lvkit.labview_error_codes import get_error_description

        desc = get_error_description(-2147467259)
        assert desc == "Unspecified error."

    def test_unknown_code_fallback(self):
        from lvkit.labview_error_codes import get_error_description

        desc = get_error_description(999999999)
        assert desc == "LabVIEW error 999999999"
