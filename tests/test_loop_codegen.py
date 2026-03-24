"""Tests for loop code generation (_make_var_name, _singularize, tracing)."""

from __future__ import annotations

import ast

import pytest

from vipy.agent.codegen.context import CodeGenContext
from vipy.agent.codegen.nodes.loop import LoopCodeGen, _negate_condition
from vipy.graph_types import Operation, Tunnel, Wire

from tests.conftest import make_ctx


class TestMakeVarName:
    """Tests for LoopCodeGen._make_var_name()."""

    @pytest.fixture
    def loop_codegen(self) -> LoopCodeGen:
        """Create a LoopCodeGen instance."""
        return LoopCodeGen()

    def test_make_var_name_from_source_terminal(self, loop_codegen: LoopCodeGen):
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

        var_name = loop_codegen._make_var_name(tunnel, ctx)
        assert var_name == "input_path"

    def test_make_var_name_from_dest_terminal(self, loop_codegen: LoopCodeGen):
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

        var_name = loop_codegen._make_var_name(tunnel, ctx)
        assert var_name == "final_count"

    def test_make_var_name_fallback_for_lsr_shift_register(
        self, loop_codegen: LoopCodeGen
    ):
        """Test fallback naming for shift register tunnels."""
        ctx = CodeGenContext()

        tunnel = Tunnel(
            outer_terminal_uid="tun_outer",
            inner_terminal_uid="tun_inner",
            tunnel_type="lSR",
        )

        var_name = loop_codegen._make_var_name(tunnel, ctx)
        # Should use generic shift register name
        assert var_name == "state"

    def test_make_var_name_fallback_for_lmax_accumulator(
        self, loop_codegen: LoopCodeGen
    ):
        """Test fallback naming for lMax accumulator tunnels."""
        ctx = CodeGenContext()

        tunnel = Tunnel(
            outer_terminal_uid="tun_outer",
            inner_terminal_uid="tun_inner",
            tunnel_type="lMax",
        )

        var_name = loop_codegen._make_var_name(tunnel, ctx)
        assert var_name == "results"

    def test_make_var_name_generic_fallback(self, loop_codegen: LoopCodeGen):
        """Test generic fallback for unknown tunnel types."""
        ctx = CodeGenContext()

        tunnel = Tunnel(
            outer_terminal_uid="tun_outer",
            inner_terminal_uid="tun_inner",
            tunnel_type="lpTun",  # Loop tunnel, not lSR or lMax
        )

        var_name = loop_codegen._make_var_name(tunnel, ctx)
        assert var_name == "value"


class TestSingularize:
    """Tests for LoopCodeGen._singularize()."""

    @pytest.fixture
    def loop_codegen(self) -> LoopCodeGen:
        """Create a LoopCodeGen instance."""
        return LoopCodeGen()

    def test_singularize_basic_plural(self, loop_codegen: LoopCodeGen):
        """Test singularizing basic plural forms ending in 's'."""
        ctx = CodeGenContext()
        assert loop_codegen._singularize("methods", ctx) == "method"
        assert loop_codegen._singularize("items", ctx) == "item"
        assert loop_codegen._singularize("values", ctx) == "value"
        assert loop_codegen._singularize("paths", ctx) == "path"

    def test_singularize_ies_ending(self, loop_codegen: LoopCodeGen):
        """Test singularizing words ending in 'ies'."""
        ctx = CodeGenContext()
        assert loop_codegen._singularize("entries", ctx) == "entry"
        assert loop_codegen._singularize("properties", ctx) == "property"

    def test_singularize_ses_xes_ches_endings(self, loop_codegen: LoopCodeGen):
        """Test singularizing words ending in 'ses', 'xes', 'ches'."""
        ctx = CodeGenContext()
        assert loop_codegen._singularize("boxes", ctx) == "box"
        assert loop_codegen._singularize("matches", ctx) == "match"

    def test_singularize_data(self, loop_codegen: LoopCodeGen):
        """Test singularizing 'data' to 'datum'."""
        ctx = CodeGenContext()
        assert loop_codegen._singularize("data", ctx) == "datum"

    def test_singularize_array(self, loop_codegen: LoopCodeGen):
        """Test singularizing 'array' to 'element'."""
        ctx = CodeGenContext()
        assert loop_codegen._singularize("array", ctx) == "element"

    def test_singularize_non_plural(self, loop_codegen: LoopCodeGen):
        """Test singularizing non-plural words adds '_item'."""
        ctx = CodeGenContext()
        assert loop_codegen._singularize("config", ctx) == "config_item"

    def test_singularize_conflict_resolution(self, loop_codegen: LoopCodeGen):
        """Test that conflicts with existing bindings are resolved."""
        ctx = make_ctx("t1")
        ctx.bind("t1", "method")  # 'method' is already used

        # Should add suffix to avoid conflict
        result = loop_codegen._singularize("methods", ctx)
        assert result == "method_2"

    def test_singularize_multiple_conflicts(self, loop_codegen: LoopCodeGen):
        """Test resolving multiple conflicts."""
        ctx = make_ctx("t1", "t2")
        ctx.bind("t1", "item")
        ctx.bind("t2", "item_2")

        result = loop_codegen._singularize("items", ctx)
        assert result == "item_3"


class TestGetSourceTerminalName:
    """Tests for LoopCodeGen._get_source_terminal_name()."""

    @pytest.fixture
    def loop_codegen(self) -> LoopCodeGen:
        return LoopCodeGen()

    def test_get_source_terminal_name_direct(self, loop_codegen: LoopCodeGen):
        """Test getting name from direct source parent."""
        data_flow = [
            Wire.from_terminals(
                from_terminal_id="src1",
                to_terminal_id="dest1",
                from_parent_name="My Input",
            ),
        ]
        ctx = CodeGenContext.from_wires(data_flow)

        name = loop_codegen._get_source_terminal_name("dest1", ctx)
        assert name == "My Input"

    def test_get_source_terminal_name_recursive(self, loop_codegen: LoopCodeGen):
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

        name = loop_codegen._get_source_terminal_name("dest1", ctx)
        assert name == "Original Source"

    def test_get_source_terminal_name_not_found(self, loop_codegen: LoopCodeGen):
        """Test returns None when no name found."""
        ctx = CodeGenContext()

        name = loop_codegen._get_source_terminal_name("unknown", ctx)
        assert name is None


class TestGetDestTerminalName:
    """Tests for LoopCodeGen._get_dest_terminal_name()."""

    @pytest.fixture
    def loop_codegen(self) -> LoopCodeGen:
        return LoopCodeGen()

    def test_get_dest_terminal_name_indicator(self, loop_codegen: LoopCodeGen):
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

        name = loop_codegen._get_dest_terminal_name("src1", ctx)
        assert name == "Output Result"

    def test_get_dest_terminal_name_subvi_param(self, loop_codegen: LoopCodeGen):
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

        name = loop_codegen._get_dest_terminal_name("src1", ctx)
        assert name == "Helper.vi"

    def test_get_dest_terminal_name_not_found(self, loop_codegen: LoopCodeGen):
        """Test returns None when no name found."""
        ctx = CodeGenContext()

        name = loop_codegen._get_dest_terminal_name("unknown", ctx)
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
    """Integration tests for LoopCodeGen.generate()."""

    @pytest.fixture
    def loop_codegen(self) -> LoopCodeGen:
        return LoopCodeGen()

    def test_generate_for_loop_with_single_array_uses_enumerate(
        self, loop_codegen: LoopCodeGen
    ):
        """Test for loop with single array input uses enumerate pattern."""
        data_flow = [
            Wire.from_terminals(from_terminal_id="input_arr", to_terminal_id="tun_outer"),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("input_arr", "items")

        loop_op = Operation(
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

        fragment = loop_codegen.generate(loop_op, ctx)

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
        assert for_loop.target.elts[0].id == "i"  # Index variable

        # Verify iter is enumerate(items)
        assert isinstance(for_loop.iter, ast.Call)
        assert for_loop.iter.func.id == "enumerate"
        assert for_loop.iter.args[0].id == "items"

    def test_generate_for_loop_with_n_terminal_uses_range(
        self, loop_codegen: LoopCodeGen
    ):
        """Test for loop with N terminal (count) uses range pattern."""
        data_flow = [
            Wire.from_terminals(from_terminal_id="count_src", to_terminal_id="lmax_outer"),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("count_src", "10")

        loop_op = Operation(
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

        fragment = loop_codegen.generate(loop_op, ctx)

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
        assert for_loop.iter.func.id == "range"

    def test_generate_while_loop_initializes_shift_register(
        self, loop_codegen: LoopCodeGen
    ):
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

        loop_op = Operation(
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

        fragment = loop_codegen.generate(loop_op, ctx)

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
        self, loop_codegen: LoopCodeGen
    ):
        """Test while loop with lMax accumulator generates append calls."""
        # lMax with incoming flow = accumulator (builds list)
        data_flow = [
            Wire.from_terminals(from_terminal_id="inner_result", to_terminal_id="lmax_inner"),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("inner_result", "computed_value")

        loop_op = Operation(
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

        fragment = loop_codegen.generate(loop_op, ctx)

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
        self, loop_codegen: LoopCodeGen
    ):
        """Test that nested loops use i, j, k for index variables."""
        # Outer loop at depth 0
        ctx_outer = CodeGenContext(loop_depth=0)

        outer_loop = Operation(
            id="outer",
            name="Outer For",
            labels=["Loop"],
            loop_type="forLoop",
            tunnels=[],
            inner_nodes=[],
        )

        outer_fragment = loop_codegen.generate(outer_loop, ctx_outer)

        # Inner loop at depth 1
        ctx_inner = CodeGenContext(loop_depth=1)

        inner_loop = Operation(
            id="inner",
            name="Inner For",
            labels=["Loop"],
            loop_type="forLoop",
            tunnels=[],
            inner_nodes=[],
        )

        inner_fragment = loop_codegen.generate(inner_loop, ctx_inner)

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
        outer_var = (
            outer_for.target.id
            if isinstance(outer_for.target, ast.Name)
            else outer_for.target.elts[0].id
        )
        inner_var = (
            inner_for.target.id
            if isinstance(inner_for.target, ast.Name)
            else inner_for.target.elts[0].id
        )

        assert outer_var == "i", f"Outer loop should use 'i', got '{outer_var}'"
        assert inner_var == "j", f"Inner loop should use 'j', got '{inner_var}'"


class TestLoopCodeGenExecutable:
    """Tests that verify generated loop code actually executes correctly."""

    @pytest.fixture
    def loop_codegen(self) -> LoopCodeGen:
        return LoopCodeGen()

    def _compile_and_run(self, statements: list[ast.stmt], local_vars: dict) -> dict:
        """Compile statements and execute, returning resulting locals."""
        module = ast.Module(body=statements, type_ignores=[])
        ast.fix_missing_locations(module)
        code = compile(module, "<test>", "exec")
        exec(code, {}, local_vars)
        return local_vars

    def test_for_loop_with_enumerate_executes(self, loop_codegen: LoopCodeGen):
        """Test that generated for loop with enumerate actually runs."""
        data_flow = [
            Wire.from_terminals(from_terminal_id="arr_src", to_terminal_id="tun_outer"),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("arr_src", "test_items")

        loop_op = Operation(
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

        fragment = loop_codegen.generate(loop_op, ctx)

        # Execute with test data
        local_vars = {"test_items": ["a", "b", "c"]}
        result = self._compile_and_run(fragment.statements, local_vars)

        # Loop should have executed (i and item should be defined from last iteration)
        assert "i" in result
        assert result["i"] == 2  # Last index

    def test_while_loop_accumulator_initializes_empty_list(
        self, loop_codegen: LoopCodeGen
    ):
        """Test that accumulator generates an empty list initialization.

        Verifying actual accumulator behavior requires a full VI context with
        inner operations. Here we verify the structural requirements:
        - Empty list initialization before the loop
        - Outer terminal is bound to the accumulator variable
        """
        data_flow = [
            Wire.from_terminals(from_terminal_id="val_src", to_terminal_id="lmax_inner"),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        ctx.bind("val_src", "iteration")

        loop_op = Operation(
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

        fragment = loop_codegen.generate(loop_op, ctx)

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
