"""Tests for loop code generation (_make_var_name, _singularize, tracing)."""

from __future__ import annotations

import ast

from lvkit.codegen.ast_utils import parse_stmt
from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes import loop
from lvkit.codegen.nodes.loop import (
    _build_while_loop,
    _expr_references,
    _get_dest_terminal_name,
    _get_source_terminal_name,
    _make_var_name,
    _negate_condition,
    _singularize,
)
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import PrimitiveNode, Wire, WireEnd
from lvkit.models import (
    LoopOperation,
    LVType,
    PrimitiveOperation,
    Terminal,
    Tunnel,
    TunnelTerminal,
)
from tests.conftest import make_ctx


class TestMakeVarName:
    """Tests for loop._make_var_name()."""

    def test_make_var_name_from_source_terminal(self):
        """Test deriving var name from source terminal name."""
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="src1",
                to_terminal_id="tun_outer",
                from_parent_name="Input Path",
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)

        tunnel = Tunnel(
            outer_terminal_uid="tun_outer",
            inner_terminal_uid="tun_inner",
            tunnel_type="lSR",
        )

        var_name = _make_var_name(tunnel, ctx)
        assert var_name == "input_path"

    def test_make_var_name_from_dest_terminal(self):
        """Test deriving var name from destination terminal name when source unnamed."""
        # Source has no name, but destination is an indicator
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="tun_outer",
                to_terminal_id="dest1",
                to_parent_name="Final Count",
                to_parent_labels=["Indicator"],
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)

        tunnel = Tunnel(
            outer_terminal_uid="tun_outer",
            inner_terminal_uid="tun_inner",
            tunnel_type="lSR",
        )

        var_name = _make_var_name(tunnel, ctx)
        assert var_name == "final_count"

    def test_make_var_name_fallback_for_lsr_shift_register(
        self    ):
        """Test fallback naming for shift register tunnels."""
        ctx = CodeGenContext()

        tunnel = Tunnel(
            outer_terminal_uid="tun_outer",
            inner_terminal_uid="tun_inner",
            tunnel_type="lSR",
        )

        var_name = _make_var_name(tunnel, ctx)
        # Should use generic shift register name
        assert var_name == "state"

    def test_make_var_name_fallback_for_lmax_accumulator(
        self    ):
        """Test fallback naming for lMax accumulator tunnels."""
        ctx = CodeGenContext()

        tunnel = Tunnel(
            outer_terminal_uid="tun_outer",
            inner_terminal_uid="tun_inner",
            tunnel_type="lMax",
        )

        var_name = _make_var_name(tunnel, ctx)
        assert var_name == "results"

    def test_make_var_name_generic_fallback(self):
        """Test generic fallback for unknown tunnel types."""
        ctx = CodeGenContext()

        tunnel = Tunnel(
            outer_terminal_uid="tun_outer",
            inner_terminal_uid="tun_inner",
            tunnel_type="lpTun",  # Loop tunnel, not lSR or lMax
        )

        var_name = _make_var_name(tunnel, ctx)
        assert var_name == "value"


class TestExprReferences:
    """Tests for loop._expr_references (accumulation vs independent SR value)."""

    def test_accumulation_references_sr(self):
        assert _expr_references("counter + 1", "counter") is True
        assert _expr_references("acc * factor", "acc") is True

    def test_independent_value_does_not_reference_sr(self):
        assert _expr_references("u16", "state") is False
        assert _expr_references("some_input + 1", "state") is False

    def test_name_is_not_substring_matched(self):
        assert _expr_references("state2 + 1", "state") is False

    def test_malformed_expression_is_safe(self):
        assert _expr_references("", "state") is False


class TestSingularize:
    """Tests for loop._singularize()."""

    def test_singularize_basic_plural(self):
        """Test singularizing basic plural forms ending in 's'."""
        ctx = CodeGenContext()
        assert _singularize("methods", ctx) == "method"
        assert _singularize("items", ctx) == "item"
        assert _singularize("values", ctx) == "value"
        assert _singularize("paths", ctx) == "path"

    def test_singularize_ies_ending(self):
        """Test singularizing words ending in 'ies'."""
        ctx = CodeGenContext()
        assert _singularize("entries", ctx) == "entry"
        assert _singularize("properties", ctx) == "property"

    def test_singularize_ses_xes_ches_endings(self):
        """Test singularizing words ending in 'ses', 'xes', 'ches'."""
        ctx = CodeGenContext()
        assert _singularize("boxes", ctx) == "box"
        assert _singularize("matches", ctx) == "match"

    def test_singularize_data(self):
        """Test singularizing 'data' to 'datum'."""
        ctx = CodeGenContext()
        assert _singularize("data", ctx) == "datum"

    def test_singularize_array(self):
        """Test singularizing 'array' to 'element'."""
        ctx = CodeGenContext()
        assert _singularize("array", ctx) == "element"

    def test_singularize_non_plural(self):
        """Test singularizing non-plural words adds '_item'."""
        ctx = CodeGenContext()
        assert _singularize("config", ctx) == "config_item"

    def test_singularize_conflict_resolution(self):
        """Test that conflicts with existing bindings are resolved."""
        ctx = make_ctx("t1")
        ctx.bind("t1", "method")  # 'method' is already used

        # Should add suffix to avoid conflict
        result = _singularize("methods", ctx)
        assert result == "method_2"

    def test_singularize_multiple_conflicts(self):
        """Test resolving multiple conflicts."""
        ctx = make_ctx("t1", "t2")
        ctx.bind("t1", "item")
        ctx.bind("t2", "item_2")

        result = _singularize("items", ctx)
        assert result == "item_3"


class TestGetSourceTerminalName:
    """Tests for loop._get_source_terminal_name()."""

    def test_get_source_terminal_name_direct(self):
        """Test getting name from direct source parent."""
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="src1",
                to_terminal_id="dest1",
                from_parent_name="My Input",
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)

        name = _get_source_terminal_name("dest1", ctx)
        assert name == "My Input"

    def test_get_source_terminal_name_recursive(self):
        """Test tracing back through multiple wires."""
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="src1",
                to_terminal_id="mid1",
                from_parent_name="Original Source",
            ),
            Wire.from_terminals(from_terminal_id="mid1", to_terminal_id="dest1"),
        ]
        ctx = CodeGenContext.from_wires(data_flow)

        name = _get_source_terminal_name("dest1", ctx)
        assert name == "Original Source"

    def test_get_source_terminal_name_not_found(self):
        """Test returns None when no name found."""
        ctx = CodeGenContext()

        name = _get_source_terminal_name("unknown", ctx)
        assert name is None


class TestGetDestTerminalName:
    """Tests for loop._get_dest_terminal_name()."""

    def test_get_dest_terminal_name_indicator(self):
        """Test getting name from indicator destination."""
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="src1",
                to_terminal_id="dest1",
                to_parent_name="Output Result",
                to_parent_labels=["Indicator"],
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)

        name = _get_dest_terminal_name("src1", ctx)
        assert name == "Output Result"

    def test_get_dest_terminal_name_subvi_param(self):
        """Test getting name from SubVI destination.

        When a value flows to a SubVI input, the SubVI name is used
        as the variable name hint. Terminal names are now populated
        directly on Terminal objects via callee_param_name.
        """
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="src1",
                to_terminal_id="subvi_term",
                to_parent_name="Helper.vi",
                to_parent_labels=["SubVI"],
                to_slot_index=0,
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)

        name = _get_dest_terminal_name("src1", ctx)
        assert name == "Helper.vi"

    def test_get_dest_terminal_name_not_found(self):
        """Test returns None when no name found."""
        ctx = CodeGenContext()

        name = _get_dest_terminal_name("unknown", ctx)
        assert name is None


class TestNegateCondition:
    """Tests for _negate_condition helper function."""

    def test_negate_double_negation(self):
        """Test that double negation is unwrapped."""
        # Create: not (not x) -> x
        inner = ast.Name(id="x", ctx=ast.Load())
        not_x = ast.UnaryOp(op=ast.Not(), operand=inner)

        result = _negate_condition(not_x)

        # Should be just 'x'
        assert isinstance(result, ast.Name)
        assert result.id == "x"

    def test_negate_comparison_eq_to_neq(self):
        """Test negating == to !=."""
        # x == y
        compare = ast.Compare(
            left=ast.Name(id="x", ctx=ast.Load()),
            ops=[ast.Eq()],
            comparators=[ast.Name(id="y", ctx=ast.Load())],
        )

        result = _negate_condition(compare)

        assert isinstance(result, ast.Compare)
        assert isinstance(result.ops[0], ast.NotEq)

    def test_negate_comparison_lt_to_gte(self):
        """Test negating < to >=."""
        compare = ast.Compare(
            left=ast.Name(id="x", ctx=ast.Load()),
            ops=[ast.Lt()],
            comparators=[ast.Constant(value=10)],
        )

        result = _negate_condition(compare)

        assert isinstance(result, ast.Compare)
        assert isinstance(result.ops[0], ast.GtE)

    def test_negate_comparison_gt_to_lte(self):
        """Test negating > to <=."""
        compare = ast.Compare(
            left=ast.Name(id="x", ctx=ast.Load()),
            ops=[ast.Gt()],
            comparators=[ast.Constant(value=0)],
        )

        result = _negate_condition(compare)

        assert isinstance(result, ast.Compare)
        assert isinstance(result.ops[0], ast.LtE)

    def test_negate_generic_wraps_in_not(self):
        """Test that generic expressions are wrapped in 'not'."""
        # func_call() -> not func_call()
        call = ast.Call(
            func=ast.Name(id="check", ctx=ast.Load()),
            args=[],
            keywords=[],
        )

        result = _negate_condition(call)

        assert isinstance(result, ast.UnaryOp)
        assert isinstance(result.op, ast.Not)
        assert isinstance(result.operand, ast.Call)


class TestLoopCodeGenGenerate:
    """Integration tests for loop.generate()."""

    def test_generate_for_loop_with_single_array_uses_enumerate(
        self    ):
        """Test for loop with single array input uses enumerate pattern."""
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="input_arr", to_terminal_id="tun_outer"
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("input_arr", "items")

        loop_op = LoopOperation(
            id="loop1",
            name="For Loop",
            labels=["Loop"],
            loop_type="forLoop",
            tunnels=[
                Tunnel(
                    outer_terminal_uid="tun_outer",
                    inner_terminal_uid="tun_inner",
                    tunnel_type="lpTun",
                ),
            ],
            inner_nodes=[],
        )

        fragment = loop.generate(loop_op, ctx)

        # Find the For loop
        for_loop = None
        for stmt in fragment.statements:
            if isinstance(stmt, ast.For):
                for_loop = stmt
                break
        assert for_loop is not None

        # Verify enumerate pattern: for i, item in enumerate(items)
        assert isinstance(for_loop.target, ast.Tuple), "Should unpack (i, item)"
        assert len(for_loop.target.elts) == 2
        assert isinstance(for_loop.target.elts[0], ast.Name)
        assert for_loop.target.elts[0].id == "i"  # Index variable

        # Verify iter is enumerate(items)
        assert isinstance(for_loop.iter, ast.Call)
        assert isinstance(for_loop.iter.func, ast.Name)
        assert for_loop.iter.func.id == "enumerate"
        assert isinstance(for_loop.iter.args[0], ast.Name)
        assert for_loop.iter.args[0].id == "items"

    def test_generate_for_loop_with_n_terminal_uses_range(
        self    ):
        """Test for loop with N terminal (count) uses range pattern."""
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="count_src", to_terminal_id="lmax_outer"
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("count_src", "10")

        loop_op = LoopOperation(
            id="loop1",
            name="For Loop",
            labels=["Loop"],
            loop_type="forLoop",
            tunnels=[
                # lMax with no incoming flow to inner = N terminal
                Tunnel(
                    outer_terminal_uid="lmax_outer",
                    inner_terminal_uid="lmax_inner",
                    tunnel_type="lMax",
                ),
            ],
            inner_nodes=[],
        )

        fragment = loop.generate(loop_op, ctx)

        for_loop = None
        for stmt in fragment.statements:
            if isinstance(stmt, ast.For):
                for_loop = stmt
                break
        assert for_loop is not None

        # Verify range pattern: for i in range(10)
        assert isinstance(for_loop.target, ast.Name)
        assert for_loop.target.id == "i"

        assert isinstance(for_loop.iter, ast.Call)
        assert isinstance(for_loop.iter.func, ast.Name)
        assert for_loop.iter.func.id == "range"

    def test_generate_while_loop_initializes_shift_register(
        self    ):
        """Test while loop with shift register initializes variable correctly."""
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="init_val",
                to_terminal_id="lsr_outer",
                from_parent_name="Counter",
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("init_val", "0")

        loop_op = LoopOperation(
            id="loop1",
            name="While Loop",
            labels=["Loop"],
            loop_type="whileLoop",
            tunnels=[
                Tunnel(
                    outer_terminal_uid="lsr_outer",
                    inner_terminal_uid="lsr_inner",
                    tunnel_type="lSR",
                ),
            ],
            inner_nodes=[],
        )

        fragment = loop.generate(loop_op, ctx)

        # Find initialization assignment (before the while loop)
        init_assign = None
        while_loop = None
        for stmt in fragment.statements:
            if isinstance(stmt, ast.Assign) and init_assign is None:
                init_assign = stmt
            if isinstance(stmt, ast.While):
                while_loop = stmt

        assert init_assign is not None, "Should have initialization statement"
        assert while_loop is not None, "Should have while loop"

        # Verify initialization: counter = 0
        ast.fix_missing_locations(init_assign)
        init_code = ast.unparse(init_assign)
        assert "= 0" in init_code, f"Should initialize to 0, got: {init_code}"

    def test_generate_while_loop_accumulator_appends_values(
        self    ):
        """Test while loop with lMax accumulator generates append calls."""
        # lMax with incoming flow = accumulator (builds list)
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="inner_result", to_terminal_id="lmax_inner"
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("inner_result", "computed_value")

        loop_op = LoopOperation(
            id="loop1",
            name="While Loop",
            labels=["Loop"],
            loop_type="whileLoop",
            tunnels=[
                Tunnel(
                    outer_terminal_uid="lmax_outer",
                    inner_terminal_uid="lmax_inner",
                    tunnel_type="lMax",
                ),
            ],
            inner_nodes=[],
        )

        fragment = loop.generate(loop_op, ctx)

        # Find list initialization and while loop
        init_assign = None
        while_loop = None
        for stmt in fragment.statements:
            if isinstance(stmt, ast.Assign):
                init_assign = stmt
            if isinstance(stmt, ast.While):
                while_loop = stmt

        assert init_assign is not None
        assert while_loop is not None

        # Verify initialization is empty list
        ast.fix_missing_locations(init_assign)
        init_code = ast.unparse(init_assign)
        assert "= []" in init_code, f"Should init empty list, got: {init_code}"

        # Verify append call exists in loop body
        ast.fix_missing_locations(while_loop)
        loop_code = ast.unparse(while_loop)
        assert ".append(" in loop_code, f"Should have append call, got: {loop_code}"
        assert "computed_value" in loop_code, "Should append the inner result"

        # Verify outer terminal is bound to accumulator
        assert "lmax_outer" in fragment.bindings

    def test_generate_nested_loop_uses_different_index_vars(
        self    ):
        """Test that nested loops use i, j, k for index variables."""
        # Outer loop at depth 0
        ctx_outer = CodeGenContext(loop_depth=0)

        outer_loop = LoopOperation(
            id="outer",
            name="Outer For",
            labels=["Loop"],
            loop_type="forLoop",
            tunnels=[],
            inner_nodes=[],
        )

        outer_fragment = loop.generate(outer_loop, ctx_outer)

        # Inner loop at depth 1
        ctx_inner = CodeGenContext(loop_depth=1)

        inner_loop = LoopOperation(
            id="inner",
            name="Inner For",
            labels=["Loop"],
            loop_type="forLoop",
            tunnels=[],
            inner_nodes=[],
        )

        inner_fragment = loop.generate(inner_loop, ctx_inner)

        # Find both for loops
        def find_for_loop(stmts):
            for s in stmts:
                if isinstance(s, ast.For):
                    return s
            return None

        outer_for = find_for_loop(outer_fragment.statements)
        inner_for = find_for_loop(inner_fragment.statements)

        assert outer_for is not None
        assert inner_for is not None

        # Outer should use 'i', inner should use 'j'
        if isinstance(outer_for.target, ast.Name):
            outer_var = outer_for.target.id
        else:
            assert isinstance(outer_for.target, ast.Tuple)
            first_elt = outer_for.target.elts[0]
            assert isinstance(first_elt, ast.Name)
            outer_var = first_elt.id
        if isinstance(inner_for.target, ast.Name):
            inner_var = inner_for.target.id
        else:
            assert isinstance(inner_for.target, ast.Tuple)
            first_elt = inner_for.target.elts[0]
            assert isinstance(first_elt, ast.Name)
            inner_var = first_elt.id

        assert outer_var == "i", f"Outer loop should use 'i', got '{outer_var}'"
        assert inner_var == "j", f"Inner loop should use 'j', got '{inner_var}'"


class TestLoopCodeGenExecutable:
    """Tests that verify generated loop code actually executes correctly."""

    def _compile_and_run(self, statements: list[ast.stmt], local_vars: dict) -> dict:
        """Compile statements and execute, returning resulting locals."""
        module = ast.Module(body=statements, type_ignores=[])
        ast.fix_missing_locations(module)
        code = compile(module, "<test>", "exec")
        exec(code, {}, local_vars)
        return local_vars

    def test_for_loop_with_enumerate_executes(self):
        """Test that generated for loop with enumerate actually runs."""
        data_flow = [
            Wire.from_terminals(from_terminal_id="arr_src", to_terminal_id="tun_outer"),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("arr_src", "test_items")

        loop_op = LoopOperation(
            id="loop1",
            name="For Loop",
            labels=["Loop"],
            loop_type="forLoop",
            tunnels=[
                Tunnel(
                    outer_terminal_uid="tun_outer",
                    inner_terminal_uid="tun_inner",
                    tunnel_type="lpTun",
                ),
            ],
            inner_nodes=[],
        )

        fragment = loop.generate(loop_op, ctx)

        # Execute with test data
        local_vars = {"test_items": ["a", "b", "c"]}
        result = self._compile_and_run(fragment.statements, local_vars)

        # Loop should have executed (i and item should be defined from last iteration)
        assert "i" in result
        assert result["i"] == 2  # Last index

    def test_for_loop_shift_register_accumulates(self):
        """Task #103: a shift register whose new value depends on its CURRENT
        value must be fed back each iteration so it accumulates. SR starts at 0,
        body computes SR = SR + 1 over range(5); result must be 5 — not 1 (the
        pre-fix behaviour, where the lSR local was never updated so every
        iteration recomputed seed + 1)."""
        data_flow = [
            Wire.from_terminals(from_terminal_id="n_src", to_terminal_id="n_outer"),
            Wire.from_terminals(
                from_terminal_id="seed_src", to_terminal_id="lsr_outer"
            ),
            Wire.from_terminals(
                from_terminal_id="lsr_inner", to_terminal_id="add_x"
            ),
            Wire.from_terminals(
                from_terminal_id="add_out", to_terminal_id="rsr_inner"
            ),
            Wire.from_terminals(
                from_terminal_id="one_src", to_terminal_id="add_y"
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("n_src", "5")
        ctx.bind("seed_src", "0")
        ctx.bind("one_src", "1")

        add = PrimitiveOperation(
            id="add", name="Add", labels=["Primitive"], primResID=1050,
            terminals=[
                Terminal(id="add_x", index=1, direction="input", name="x"),
                Terminal(id="add_y", index=2, direction="input", name="y"),
                Terminal(id="add_out", index=0, direction="output", name="result"),
            ],
        )
        loop_op = LoopOperation(
            id="loop1", name="For Loop", labels=["Loop"], loop_type="forLoop",
            tunnels=[
                Tunnel(outer_terminal_uid="n_outer",
                       inner_terminal_uid="n_inner", tunnel_type="lMax"),
                Tunnel(outer_terminal_uid="lsr_outer",
                       inner_terminal_uid="lsr_inner", tunnel_type="lSR"),
                Tunnel(outer_terminal_uid="rsr_outer",
                       inner_terminal_uid="rsr_inner", tunnel_type="rSR"),
            ],
            inner_nodes=[add],
        )

        fragment = loop.generate(loop_op, ctx)
        acc_var = fragment.bindings.get("rsr_outer")
        assert acc_var is not None, "rSR outer terminal should be bound"
        result = self._compile_and_run(fragment.statements, {})
        assert result[acc_var] == 5

    def test_while_loop_accumulator_initializes_empty_list(
        self    ):
        """Test that accumulator generates an empty list initialization.

        Verifying actual accumulator behavior requires a full VI context with
        inner operations. Here we verify the structural requirements:
        - Empty list initialization before the loop
        - Outer terminal is bound to the accumulator variable
        """
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="val_src", to_terminal_id="lmax_inner"
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("val_src", "iteration")

        loop_op = LoopOperation(
            id="loop1",
            name="While Loop",
            labels=["Loop"],
            loop_type="whileLoop",
            tunnels=[
                Tunnel(
                    outer_terminal_uid="lmax_outer",
                    inner_terminal_uid="lmax_inner",
                    tunnel_type="lMax",
                ),
            ],
            inner_nodes=[],
            stop_condition_terminal="stop_term",
        )

        fragment = loop.generate(loop_op, ctx)

        # Find list initialization
        init_assigns = [s for s in fragment.statements if isinstance(s, ast.Assign)]
        assert len(init_assigns) >= 1, "Should have at least one initialization"

        # One of them should initialize to []
        found_list_init = False
        for assign in init_assigns:
            ast.fix_missing_locations(assign)
            code = ast.unparse(assign)
            if "= []" in code:
                found_list_init = True
                break
        assert found_list_init, "Should initialize accumulator to empty list"

        # Outer terminal should be bound
        accum_var = fragment.bindings.get("lmax_outer")
        assert accum_var is not None, "Outer terminal should be bound to accumulator"

        # Accumulator should be the list variable
        assert accum_var in [
            assign.targets[0].id
            for assign in init_assigns
            if isinstance(assign.targets[0], ast.Name)
        ]


class TestWhileLoopDoWhileSemantics:
    """Task #19: LabVIEW while loops are do-while (body runs at least once,
    condition tested at the END) AND honor conditional-terminal polarity
    (Stop-if-True vs Continue-if-True).

    These tests build the ``ast.While`` via ``_build_while_loop`` directly
    (the exact function this task rewrites), then compile and EXECUTE the
    resulting module, asserting on actual runtime values -- not just on the
    shape of the generated AST.
    """

    def _run(self, while_ast: ast.While, preamble: str) -> dict:
        module = ast.Module(
            body=[parse_stmt(preamble), while_ast],
            type_ignores=[],
        )
        ast.fix_missing_locations(module)
        local_vars: dict = {}
        exec(compile(module, "<test>", "exec"), {}, local_vars)
        return local_vars

    def test_body_runs_once_even_when_stop_if_true_is_immediately_true(self):
        """Do-while: the stop condition is computed INSIDE the body and is
        True on the very first pass. A pre-test ``while not stop`` loop
        would never enter the body at all. LabVIEW semantics run the body
        first, then test -- so it must still execute exactly once."""
        ctx = make_ctx("stop_flag")
        ctx.bind("stop_flag", "should_stop")

        node = LoopOperation(
            id="loop1",
            name="While Loop",
            labels=["Loop"],
            loop_type="whileLoop",
            tunnels=[],
            inner_nodes=[],
            stop_condition_terminal="stop_flag",
            stop_condition_inverted=False,  # Stop-if-True
        )
        body = [
            parse_stmt("run_count += 1"),
            parse_stmt("should_stop = True"),
        ]

        while_ast, stop_var = _build_while_loop(node, body, ctx)
        assert stop_var == "should_stop"

        result = self._run(while_ast, "run_count = 0")
        assert result["run_count"] == 1

    def test_stop_if_true_terminates_with_correct_final_value(self):
        """Stop-if-True (default polarity): loop breaks when the resolved
        condition becomes True -- ``if cond: break``."""
        ctx = make_ctx("stop_flag")
        ctx.bind("stop_flag", "should_stop")

        node = LoopOperation(
            id="loop1",
            name="While Loop",
            labels=["Loop"],
            loop_type="whileLoop",
            tunnels=[],
            inner_nodes=[],
            stop_condition_terminal="stop_flag",
            stop_condition_inverted=False,  # Stop-if-True
        )
        body = [
            parse_stmt("count += 1"),
            parse_stmt("should_stop = count >= 3"),
        ]

        while_ast, _ = _build_while_loop(node, body, ctx)

        result = self._run(while_ast, "count = 0")
        assert result["count"] == 3

    def test_continue_if_true_terminates_with_correct_final_value(self):
        """Continue-if-True (inverted polarity): the loop keeps running
        while the condition is True, so it breaks when the condition is
        False -- ``if not cond: break``.

        Uses the opposite condition expression (``count < 3`` instead of
        ``count >= 3``) to reach the SAME final count as the Stop-if-True
        test. If the polarity were mishandled (e.g. treated as
        Stop-if-True), this would incorrectly break after the first
        iteration (count == 1) instead of running to count == 3.
        """
        ctx = make_ctx("continue_flag")
        ctx.bind("continue_flag", "keep_going")

        node = LoopOperation(
            id="loop1",
            name="While Loop",
            labels=["Loop"],
            loop_type="whileLoop",
            tunnels=[],
            inner_nodes=[],
            stop_condition_terminal="continue_flag",
            stop_condition_inverted=True,  # Continue-if-True
        )
        body = [
            parse_stmt("count += 1"),
            parse_stmt("keep_going = count < 3"),
        ]

        while_ast, _ = _build_while_loop(node, body, ctx)

        result = self._run(while_ast, "count = 0")
        assert result["count"] == 3

    def test_generate_end_to_end_do_while_shape(self):
        """End-to-end through loop.generate(): the emitted AST is the
        do-while shape (``while True: <body>; if <stop>: break``), not the
        old pre-test shape (``while not <stop>: <body>``)."""
        ctx = make_ctx("stop_flag")
        ctx.bind("stop_flag", "should_stop")

        loop_op = LoopOperation(
            id="loop1",
            name="While Loop",
            labels=["Loop"],
            loop_type="whileLoop",
            tunnels=[],
            inner_nodes=[],
            stop_condition_terminal="stop_flag",
            stop_condition_inverted=False,
        )

        fragment = loop.generate(loop_op, ctx)

        while_stmt = next(
            s for s in fragment.statements if isinstance(s, ast.While)
        )
        # Top-level test must be `True` (do-while), never a computed
        # pre-test condition.
        assert isinstance(while_stmt.test, ast.Constant)
        assert while_stmt.test.value is True

        # An `if <stop>: break` must appear as the last statement in the
        # loop body (condition tested at the END, not the start).
        last = while_stmt.body[-1]
        assert isinstance(last, ast.If)
        assert len(last.body) == 1
        assert isinstance(last.body[0], ast.Break)


class TestForLoopConditionalTerminal:
    """A For Loop may carry an OPTIONAL conditional terminal (LabVIEW 2012+).
    When present it must emit a guarded break (tested at end of iteration, like
    a while loop); when absent, a plain for loop with NO break."""

    def _for_with_count(self, stop=None, inverted=False):
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="count_src", to_terminal_id="lmax_outer"
            ),
        ]
        if stop is not None:
            data_flow.append(
                Wire.from_terminals(from_terminal_id="cond_calc", to_terminal_id=stop)
            )
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("count_src", "10")
        if stop is not None:
            ctx.bind("cond_calc", "should_stop")
        loop_op = LoopOperation(
            id="loop1", name="For Loop", labels=["Loop"], loop_type="forLoop",
            tunnels=[Tunnel(outer_terminal_uid="lmax_outer",
                            inner_terminal_uid="lmax_inner", tunnel_type="lMax")],
            inner_nodes=[],
            stop_condition_terminal=stop,
            stop_condition_inverted=inverted,
        )
        frag = loop.generate(loop_op, ctx)
        return next(s for s in frag.statements if isinstance(s, ast.For))

    def test_no_break_when_no_conditional(self):
        for_loop = self._for_with_count(stop=None)
        assert not any(isinstance(s, ast.Break) for s in ast.walk(for_loop))

    def test_break_when_stop_if_true(self):
        for_loop = self._for_with_count(stop="stop_term", inverted=False)
        ifs = [s for s in for_loop.body if isinstance(s, ast.If)]
        assert ifs and any(isinstance(b, ast.Break) for b in ifs[-1].body)
        # Stop-if-True: break on the bare condition (no `not`)
        assert not isinstance(ifs[-1].test, ast.UnaryOp)

    def test_break_when_continue_if_true(self):
        for_loop = self._for_with_count(stop="stop_term", inverted=True)
        ifs = [s for s in for_loop.body if isinstance(s, ast.If)]
        assert ifs and isinstance(ifs[-1].test, ast.UnaryOp)  # `if not cond: break`
        assert isinstance(ifs[-1].test.op, ast.Not)


class TestUninitializedShiftRegister:
    """An lSR whose outer (left) terminal has nothing wired in from
    outside the loop is the LV2/functional-global idiom: the value
    PERSISTS ACROSS CALLS to the VI. loop.py must lower this to a
    module-level global (CodeGenContext.module_globals) instead of
    silently dropping the shift register (the old behavior)."""

    def _ctx_and_terminals(self, lv_type: LVType) -> tuple[CodeGenContext, list]:
        """Graph for a while loop whose rSR inner terminal is wired
        directly from an external source ('new_val_src') -- mirrors the
        real U16 Changed__ogtk.vi shape, where the current input value is
        wired straight into the shift register with no inner primitive
        nodes at all."""
        graph = InMemoryVIGraph()

        src_node = PrimitiveNode(
            id="src", vi="test.vi", name="src",
            terminals=[Terminal(id="new_val_src", index=0, direction="output")],
        )
        graph._graph.add_node("src", node=src_node)
        graph._term_to_node["new_val_src"] = "src"

        loop_terminals = [
            TunnelTerminal(
                id="lsr_outer", index=1, direction="input",
                tunnel_type="lSR", boundary="outer", lv_type=lv_type,
            ),
            TunnelTerminal(
                id="lsr_inner", index=2, direction="output",
                tunnel_type="lSR", boundary="inner", lv_type=lv_type,
            ),
            TunnelTerminal(
                id="rsr_outer", index=3, direction="output",
                tunnel_type="rSR", boundary="outer", lv_type=lv_type,
            ),
            TunnelTerminal(
                id="rsr_inner", index=4, direction="input",
                tunnel_type="rSR", boundary="inner", lv_type=lv_type,
            ),
        ]
        loop_node = PrimitiveNode(
            id="loop1", vi="test.vi", name="loop1", terminals=loop_terminals,
        )
        graph._graph.add_node("loop1", node=loop_node)
        for t in loop_terminals:
            graph._term_to_node[t.id] = "loop1"

        graph._graph.add_edge(
            "src", "loop1",
            source=WireEnd(terminal_id="new_val_src", node_id="src"),
            dest=WireEnd(terminal_id="rsr_inner", node_id="loop1"),
        )

        ctx = CodeGenContext(graph=graph)
        ctx.bind("new_val_src", "new_value")
        return ctx, loop_terminals

    def _loop_op(self, terminals: list) -> LoopOperation:
        return LoopOperation(
            id="loop1",
            name="While Loop",
            labels=["Loop"],
            loop_type="whileLoop",
            terminals=terminals,
            tunnels=[
                Tunnel(
                    outer_terminal_uid="lsr_outer",
                    inner_terminal_uid="lsr_inner",
                    tunnel_type="lSR",
                ),
                Tunnel(
                    outer_terminal_uid="rsr_outer",
                    inner_terminal_uid="rsr_inner",
                    tunnel_type="rSR",
                ),
            ],
            inner_nodes=[],
        )

    def test_registers_module_global_seeded_to_type_default(self):
        lv_type = LVType(kind="primitive", underlying_type="NumUInt16")
        ctx, terminals = self._ctx_and_terminals(lv_type)
        loop_op = self._loop_op(terminals)

        loop.generate(loop_op, ctx)

        assert len(ctx.module_globals) == 1
        name = next(iter(ctx.module_globals))
        assert name.startswith("_lv_state_")
        stmt = ctx.module_globals[name]
        code = ast.unparse(ast.fix_missing_locations(stmt))
        assert code == f"{name} = 0"

    def test_emits_global_declaration_and_seeds_local_from_it(self):
        lv_type = LVType(kind="primitive", underlying_type="NumUInt16")
        ctx, terminals = self._ctx_and_terminals(lv_type)
        loop_op = self._loop_op(terminals)

        fragment = loop.generate(loop_op, ctx)
        global_name = next(iter(ctx.module_globals))

        global_stmts = [
            s for s in fragment.statements if isinstance(s, ast.Global)
        ]
        assert any(global_name in g.names for g in global_stmts)

        # First statement after the global decl seeds the local from it:
        # shift_var = <global_name>
        seed = fragment.statements[1]
        assert isinstance(seed, ast.Assign)
        ast.fix_missing_locations(seed)
        assert ast.unparse(seed).endswith(f"= {global_name}")

    def test_writes_back_to_global_after_loop(self):
        lv_type = LVType(kind="primitive", underlying_type="NumUInt16")
        ctx, terminals = self._ctx_and_terminals(lv_type)
        loop_op = self._loop_op(terminals)

        fragment = loop.generate(loop_op, ctx)
        global_name = next(iter(ctx.module_globals))

        writeback = fragment.statements[-1]
        assert isinstance(writeback, ast.Assign)
        ast.fix_missing_locations(writeback)
        assert ast.unparse(writeback).startswith(f"{global_name} =")

    def test_persists_and_updates_across_two_calls_to_compiled_function(self):
        """The real correctness bar: COMPILE the generated statements into
        a function and CALL IT TWICE, proving the shift register value
        (a) starts at the type default, (b) is visible to the NEXT call,
        and (c) is updated to whatever was wired into rSR each call --
        exactly the LV2/functional-global semantics this lowering exists
        for."""
        lv_type = LVType(kind="primitive", underlying_type="NumUInt16")
        ctx, terminals = self._ctx_and_terminals(lv_type)
        loop_op = self._loop_op(terminals)

        fragment = loop.generate(loop_op, ctx)
        global_name = next(iter(ctx.module_globals))
        # The rSR's outer-terminal binding is the freshly WRITTEN value
        # (matches rSR's real dataflow meaning); the lSR-bound local from
        # the seed assignment holds the unmutated PREVIOUS value -- both
        # must remain independently readable within the same call (see
        # loop.py step 5's docstring for why the update does not mutate
        # the lSR-bound local in place).
        new_var = fragment.bindings["rsr_outer"]
        old_var = fragment.statements[1].targets[0].id
        assert old_var != new_var

        func_def = ast.FunctionDef(
            name="run",
            args=ast.arguments(
                posonlyargs=[], args=[ast.arg(arg="new_value")], vararg=None,
                kwonlyargs=[], kw_defaults=[], kwarg=None, defaults=[],
            ),
            body=[
                *fragment.statements,
                ast.Return(
                    value=ast.Tuple(
                        elts=[
                            ast.Name(id=old_var, ctx=ast.Load()),
                            ast.Name(id=new_var, ctx=ast.Load()),
                        ],
                        ctx=ast.Load(),
                    )
                ),
            ],
            decorator_list=[],
        )
        module = ast.Module(
            body=[ctx.module_globals[global_name], func_def], type_ignores=[],
        )
        ast.fix_missing_locations(module)
        ns: dict = {}
        exec(compile(module, "<test>", "exec"), ns)  # noqa: S102
        run = ns["run"]

        # Call 1: previous value is the type default (0); new_value=5 is
        # written into the SR for next time.
        assert run(5) == (0, 5)
        assert ns[global_name] == 5

        # Call 2: sees the value persisted from call 1.
        assert run(12) == (5, 12)
        assert ns[global_name] == 12
