"""Tests for compound operation code generation (cpdArith, aBuild)."""

from __future__ import annotations

import ast

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.nodes import compound
from lvkit.models import LVType, PrimitiveOperation, Terminal
from tests.helpers import make_ctx


class TestCompoundArithMakeVarName:
    """Tests for CompoundArithCodeGen._make_var_name()."""

    def test_make_var_name_boolean_or_returns_should_stop(self):
        """Test that boolean OR operation returns 'should_stop'."""
        var_name = compound._make_arith_var_name("or", [])
        assert var_name == "should_stop"

    def test_make_var_name_boolean_and_returns_should_stop(self):
        """Test that boolean AND operation returns 'should_stop'."""
        var_name = compound._make_arith_var_name("and", [])
        assert var_name == "should_stop"

    def test_make_var_name_with_stop_keyword_input(self):
        """Test detection of stop-related keywords in input names."""
        var_name = compound._make_arith_var_name("or", ["user_stopped", "timeout"])
        assert var_name == "should_stop"

    def test_make_var_name_with_done_keyword_input(self):
        """Test detection of done-related keywords in input names."""
        var_name = compound._make_arith_var_name("or", ["is_done", "other_flag"])
        assert var_name == "should_stop"

    def test_make_var_name_add_returns_total(self):
        """Test that add operation returns 'total'."""
        var_name = compound._make_arith_var_name("add", ["x", "y", "z"])
        assert var_name == "total"

    def test_make_var_name_unknown_returns_combined(self):
        """Test that unknown operation returns 'combined'."""
        var_name = compound._make_arith_var_name("multiply", [])
        assert var_name == "combined"


class TestCompoundArithGenerate:
    """Tests for CompoundArithCodeGen.generate()."""

    def test_generate_or_two_inputs(self):
        """Test generating OR of two inputs."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "flag_a")
        ctx.bind("term2", "flag_b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Or",
            kind="primitive",
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        assert len(fragment.statements) == 1
        assert "term_out" in fragment.bindings

        # Should produce: should_stop = flag_a or flag_b
        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "should_stop" in code
        assert "flag_a" in code
        assert "flag_b" in code
        assert " or " in code

    def test_generate_and_two_inputs(self):
        """Test generating AND of two inputs."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "cond_a")
        ctx.bind("term2", "cond_b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound And",
            kind="primitive",
            node_type="cpdArith",
            operation="and",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert " and " in code

    def _int_cpd(self, operation, inverted_in=False, inverted_out=False):
        ctx = make_ctx("t1", "t2", "tout")
        ctx.bind("t1", "a")
        ctx.bind("t2", "b")
        u32 = LVType(kind="primitive", underlying_type="NumUInt32")
        op = PrimitiveOperation(
            id="cpd", name="Compound", kind="primitive",
            node_type="cpdArith", operation=operation,
            terminals=[
                Terminal(id="t1", index=1, direction="input", lv_type=u32,
                         inverted=inverted_in),
                Terminal(id="t2", index=2, direction="input", lv_type=u32),
                Terminal(id="tout", index=0, direction="output", lv_type=u32,
                         inverted=inverted_out),
            ],
        )
        frag = compound.generate_compound_arith(op, ctx)
        ast.fix_missing_locations(frag.statements[0])
        return ast.unparse(frag.statements[0])

    def test_generate_or_integer_is_bitwise(self):
        """Integer operands -> bitwise OR, not logical `or`."""
        code = self._int_cpd("or")
        assert "a | b" in code
        assert " or " not in code

    def test_generate_and_integer_is_bitwise(self):
        """Integer operands -> bitwise AND, not logical `and`."""
        code = self._int_cpd("and")
        assert "a & b" in code
        assert " and " not in code

    def test_generate_integer_invert_is_width_masked(self):
        """A per-input Not on a U32 -> ~ masked to 32 bits (not a signed ~)."""
        code = self._int_cpd("or", inverted_in=True)
        assert "4294967295" in code  # 0xFFFFFFFF mask
        assert "~a" in code

    def test_generate_add_multiple_inputs(self):
        """Test generating addition of multiple inputs."""
        ctx = make_ctx("term1", "term2", "term3", "term_out")
        ctx.bind("term1", "x")
        ctx.bind("term2", "y")
        ctx.bind("term3", "z")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Add",
            kind="primitive",
            node_type="cpdArith",
            operation="add",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term3", index=3, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "total" in code
        assert "+" in code

    def test_generate_single_input_passthrough(self):
        """Test that single input is passed through without assignment."""
        ctx = make_ctx("term1", "term_out")
        ctx.bind("term1", "only_value")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Or",
            kind="primitive",
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        # Single input should passthrough, no statements needed
        assert len(fragment.statements) == 0
        assert fragment.bindings["term_out"] == "only_value"

    def test_generate_no_inputs_default_value(self):
        """Test that no inputs produces default value."""
        ctx = CodeGenContext()

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Or",
            kind="primitive",
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        # Should produce assignment to False for boolean operation
        assert len(fragment.statements) == 1
        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "False" in code

    def test_generate_no_output_returns_empty(self):
        """Test that no output terminal returns empty fragment."""
        ctx = CodeGenContext()

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Or",
            kind="primitive",
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        assert len(fragment.statements) == 0
        assert len(fragment.bindings) == 0


class TestCompoundArithResolvesThroughDataflow:
    """Tests verifying compound ops resolve inputs through dataflow."""

    def test_or_resolves_through_wires(self):
        """Test that OR resolves inputs through wire connections."""
        from lvkit.graph.models import Wire

        # Set up dataflow: src1 -> term1, src2 -> term2
        data_flow = [
            Wire.from_terminals(from_terminal_id="src1", to_terminal_id="term1"),
            Wire.from_terminals(from_terminal_id="src2", to_terminal_id="term2"),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        # Bind at SOURCE terminals, not the cpd input terminals
        ctx.bind("src1", "error_in.status")
        ctx.bind("src2", "timeout_occurred")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Or",
            kind="primitive",
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        # Should resolve through wires to find source values
        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "error_in.status" in code, f"Should resolve src1, got: {code}"
        assert "timeout_occurred" in code, f"Should resolve src2, got: {code}"


class TestCompoundArithExecutable:
    """Tests that verify generated compound arithmetic code executes correctly."""

    def _compile_and_run(self, statements: list, local_vars: dict) -> dict:
        """Compile statements and execute, returning resulting locals."""
        module = ast.Module(body=statements, type_ignores=[])
        ast.fix_missing_locations(module)
        code = compile(module, "<test>", "exec")
        exec(code, {}, local_vars)
        return local_vars

    def test_or_evaluates_correctly(self):
        """Test that generated OR code evaluates correctly at runtime."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "flag_a")
        ctx.bind("term2", "flag_b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Or",
            kind="primitive",
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        # Test case: False or True = True
        result = self._compile_and_run(
            fragment.statements, {"flag_a": False, "flag_b": True}
        )
        output_var = fragment.bindings["term_out"]
        assert result[output_var] is True

        # Test case: False or False = False
        result = self._compile_and_run(
            fragment.statements, {"flag_a": False, "flag_b": False}
        )
        assert result[output_var] is False

    def test_and_evaluates_correctly(self):
        """Test that generated AND code evaluates correctly at runtime."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "cond_a")
        ctx.bind("term2", "cond_b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound And",
            kind="primitive",
            node_type="cpdArith",
            operation="and",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        # Test case: True and True = True
        result = self._compile_and_run(
            fragment.statements, {"cond_a": True, "cond_b": True}
        )
        output_var = fragment.bindings["term_out"]
        assert result[output_var] is True

        # Test case: True and False = False
        result = self._compile_and_run(
            fragment.statements, {"cond_a": True, "cond_b": False}
        )
        assert result[output_var] is False

    def test_add_evaluates_correctly(self):
        """Test that generated ADD code evaluates correctly at runtime."""
        ctx = make_ctx("term1", "term2", "term3", "term_out")
        ctx.bind("term1", "a")
        ctx.bind("term2", "b")
        ctx.bind("term3", "c")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Add",
            kind="primitive",
            node_type="cpdArith",
            operation="add",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term3", index=3, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        # Test: 1 + 2 + 3 = 6
        result = self._compile_and_run(
            fragment.statements, {"a": 1, "b": 2, "c": 3}
        )
        output_var = fragment.bindings["term_out"]
        assert result[output_var] == 6


class TestCompoundArithInvert:
    """Tests for per-terminal invert ("Not") on cpdArith terminals."""

    def test_boolean_add_with_inverted_input_becomes_or_not(self):
        """add on Boolean terminals is OR; an inverted input gets `not (...)`."""
        boolean = LVType(kind="primitive", underlying_type="Boolean")
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "hasTensPlace")
        ctx.bind("term2", "isTeen")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Add",
            kind="primitive",
            node_type="cpdArith",
            operation="add",
            terminals=[
                Terminal(
                    id="term1", index=1, direction="input", lv_type=boolean,
                ),
                Terminal(
                    id="term2", index=2, direction="input", lv_type=boolean,
                    inverted=True,
                ),
                Terminal(
                    id="term_out", index=0, direction="output", lv_type=boolean,
                ),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert " or " in code
        assert "not isTeen" in code
        assert "hasTensPlace" in code

        # Equivalent to: x or (not y)
        module = ast.Module(body=fragment.statements, type_ignores=[])
        ast.fix_missing_locations(module)
        compiled = compile(module, "<test>", "exec")
        output_var = fragment.bindings["term_out"]

        local_vars = {"hasTensPlace": False, "isTeen": False}
        exec(compiled, {}, local_vars)
        assert local_vars[output_var] is True  # False or (not False)

        local_vars = {"hasTensPlace": False, "isTeen": True}
        exec(compiled, {}, local_vars)
        assert local_vars[output_var] is False  # False or (not True)

        local_vars = {"hasTensPlace": True, "isTeen": True}
        exec(compiled, {}, local_vars)
        assert local_vars[output_var] is True  # True or (not True)

    def test_numeric_add_unaffected_by_uninverted_terminals(self):
        """Numeric add with no inverted terminals keeps plain `+` behavior."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "a")
        ctx.bind("term2", "b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Add",
            kind="primitive",
            node_type="cpdArith",
            operation="add",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "+" in code
        assert "not" not in code
        assert "~" not in code

    def test_inverted_output_boolean(self):
        """An inverted output terminal wraps the whole combined expr in `not`."""
        boolean = LVType(kind="primitive", underlying_type="Boolean")
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "flag_a")
        ctx.bind("term2", "flag_b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Or",
            kind="primitive",
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="term1", index=1, direction="input", lv_type=boolean),
                Terminal(id="term2", index=2, direction="input", lv_type=boolean),
                Terminal(
                    id="term_out", index=0, direction="output", lv_type=boolean,
                    inverted=True,
                ),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert code.strip().startswith(fragment.bindings["term_out"] + " = not")

        module = ast.Module(body=fragment.statements, type_ignores=[])
        ast.fix_missing_locations(module)
        compiled = compile(module, "<test>", "exec")
        output_var = fragment.bindings["term_out"]

        local_vars = {"flag_a": False, "flag_b": False}
        exec(compiled, {}, local_vars)
        assert local_vars[output_var] is True  # not (False or False)

    def _invert_op(self, operation):
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "a")
        ctx.bind("term2", "b")
        op = PrimitiveOperation(
            id="cpd1",
            name=f"Compound {operation}",
            kind="primitive",
            node_type="cpdArith",
            operation=operation,
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output", inverted=True),
            ],
        )
        fragment = compound.generate_compound_arith(op, ctx)
        ast.fix_missing_locations(fragment.statements[0])
        return ast.unparse(fragment.statements[0])

    def test_inverted_output_add_negates(self):
        """Add-mode Invert negates the output (subtraction), per NI docs -- not
        bitwise complement."""
        code = self._invert_op("add")
        assert "-(a + b)" in code
        assert "~" not in code and "not" not in code

    def test_inverted_output_multiply_uses_reciprocal(self):
        """Multiply-mode Invert produces the reciprocal (1 / product)."""
        code = self._invert_op("multiply")
        assert "1 / (a * b)" in code

    def test_inverted_output_logical_uses_bitwise_invert(self):
        """AND/OR/XOR-mode Invert on numeric operands uses `~(...)`, not `not`."""
        code = self._invert_op("and")
        assert "~" in code
        assert "not" not in code

    def test_unsupported_operation_fails_loud(self):
        """An unrecognised dcoFiller code (parser sentinel 'unsupported')
        raises at codegen rather than silently defaulting to OR."""
        import pytest

        from lvkit.codegen.nodes.base import CodeGenError
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "a")
        ctx.bind("term2", "b")
        op = PrimitiveOperation(
            id="cpd1", name="Compound ?", kind="primitive",
            node_type="cpdArith", operation="unsupported",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )
        with pytest.raises(CodeGenError, match="not supported"):
            compound.generate_compound_arith(op, ctx)

    def test_multiply_boolean_translates_to_and(self):
        """multiply on Boolean terminals combines via `and`."""
        boolean = LVType(kind="primitive", underlying_type="Boolean")
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "a")
        ctx.bind("term2", "b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Multiply",
            kind="primitive",
            node_type="cpdArith",
            operation="multiply",
            terminals=[
                Terminal(id="term1", index=1, direction="input", lv_type=boolean),
                Terminal(id="term2", index=2, direction="input", lv_type=boolean),
                Terminal(id="term_out", index=0, direction="output", lv_type=boolean),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert " and " in code

    def test_multiply_numeric_uses_mult(self):
        """multiply on numeric terminals uses `*`."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "a")
        ctx.bind("term2", "b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Multiply",
            kind="primitive",
            node_type="cpdArith",
            operation="multiply",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "*" in code

    def test_xor_boolean_uses_not_equal(self):
        """xor on Boolean terminals combines via `!=`."""
        boolean = LVType(kind="primitive", underlying_type="Boolean")
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "a")
        ctx.bind("term2", "b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Xor",
            kind="primitive",
            node_type="cpdArith",
            operation="xor",
            terminals=[
                Terminal(id="term1", index=1, direction="input", lv_type=boolean),
                Terminal(id="term2", index=2, direction="input", lv_type=boolean),
                Terminal(id="term_out", index=0, direction="output", lv_type=boolean),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "!=" in code

    def test_xor_numeric_uses_bitxor(self):
        """xor on numeric terminals uses `^`."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "a")
        ctx.bind("term2", "b")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Xor",
            kind="primitive",
            node_type="cpdArith",
            operation="xor",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
                Terminal(id="term2", index=2, direction="input"),
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_compound_arith(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "^" in code


class TestArrayBuildMakeVarName:
    """Tests for ArrayBuildCodeGen._make_var_name()."""

    def test_make_var_name_no_inputs(self):
        """Test default name when no inputs."""
        var_name = compound._make_array_var_name([])
        assert var_name == "items"

    def test_make_var_name_common_base_pluralized(self):
        """Test pluralizing common base name."""
        var_name = compound._make_array_var_name(["path_part_1", "path_part_2"])
        # Should detect "path_part" as common base and pluralize
        assert "path" in var_name

    def test_make_var_name_fallback_items(self):
        """Test fallback to 'items' when no common base."""
        var_name = compound._make_array_var_name(["x", "y", "z"])
        assert var_name == "items"

    def test_make_var_name_pluralize_y_to_ies(self):
        """Test pluralizing words ending in y to ies."""
        # If base is "entry", should become "entries"
        var_name = compound._make_array_var_name(["entry_1", "entry_2", "entry_3"])
        assert var_name == "entries"

    def test_make_var_name_pluralize_s_ending(self):
        """Test pluralizing words ending in s/x/z/ch/sh."""
        var_name = compound._make_array_var_name(["box_1", "box_2"])
        assert var_name == "boxes"


class TestArrayBuildGenerate:
    """Tests for ArrayBuildCodeGen.generate()."""

    def test_generate_builds_list(self):
        """Test that aBuild generates a list."""
        ctx = make_ctx("term1", "term2", "term3", "term_out")
        ctx.bind("term1", "val1")
        ctx.bind("term2", "val2")
        ctx.bind("term3", "val3")

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term1", index=0, direction="input"),
                Terminal(id="term2", index=1, direction="input"),
                Terminal(id="term3", index=2, direction="input"),
                Terminal(id="term_out", index=3, direction="output"),
            ],
        )

        fragment = compound.generate_array_build(op, ctx)

        assert len(fragment.statements) == 1
        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "[" in code
        assert "val1" in code
        assert "val2" in code
        assert "val3" in code

    def test_generate_handles_missing_inputs(self):
        """Test that missing inputs become None placeholders."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "val1")
        # term2 is not bound

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term1", index=0, direction="input"),
                Terminal(id="term2", index=1, direction="input"),
                Terminal(id="term_out", index=2, direction="output"),
            ],
        )

        fragment = compound.generate_array_build(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "None" in code

    def test_generate_no_output_returns_empty(self):
        """Test that no output terminal returns empty fragment."""
        ctx = CodeGenContext()

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term1", index=0, direction="input"),
            ],
        )

        fragment = compound.generate_array_build(op, ctx)

        assert len(fragment.statements) == 0
        assert len(fragment.bindings) == 0

    def test_generate_empty_array(self):
        """Test generating empty array when no inputs."""
        ctx = CodeGenContext()

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_array_build(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "[]" in code or "items = []" in code


class TestArrayBuildExecutable:
    """Tests that verify generated array build code executes correctly."""

    def _compile_and_run(self, statements: list, local_vars: dict) -> dict:
        """Compile statements and execute, returning resulting locals."""
        module = ast.Module(body=statements, type_ignores=[])
        ast.fix_missing_locations(module)
        code = compile(module, "<test>", "exec")
        exec(code, {}, local_vars)
        return local_vars

    def test_build_array_produces_correct_list(self):
        """Test that generated array build produces correct list at runtime."""
        ctx = make_ctx("term1", "term2", "term3", "term_out")
        ctx.bind("term1", "first")
        ctx.bind("term2", "second")
        ctx.bind("term3", "third")

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term1", index=0, direction="input"),
                Terminal(id="term2", index=1, direction="input"),
                Terminal(id="term3", index=2, direction="input"),
                Terminal(id="term_out", index=3, direction="output"),
            ],
        )

        fragment = compound.generate_array_build(op, ctx)

        # Execute with test data
        result = self._compile_and_run(
            fragment.statements, {"first": "a", "second": "b", "third": "c"}
        )
        output_var = fragment.bindings["term_out"]

        # Should produce ["a", "b", "c"]
        assert output_var in result
        assert result[output_var] == ["a", "b", "c"]

    def test_build_array_preserves_order(self):
        """Test that array build preserves input order based on terminal index."""
        ctx = make_ctx("term_0", "term_1", "term_2", "term_out")
        ctx.bind("term_0", "zero")
        ctx.bind("term_1", "one")
        ctx.bind("term_2", "two")

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term_2", index=2, direction="input"),  # Out of order
                Terminal(id="term_0", index=0, direction="input"),
                Terminal(id="term_1", index=1, direction="input"),
                Terminal(id="term_out", index=3, direction="output"),
            ],
        )

        fragment = compound.generate_array_build(op, ctx)

        result = self._compile_and_run(
            fragment.statements, {"zero": 0, "one": 1, "two": 2}
        )
        output_var = fragment.bindings["term_out"]

        # Should be ordered by index: [0, 1, 2]
        assert result[output_var] == [0, 1, 2]

    def test_build_empty_array_produces_empty_list(self):
        """Test that empty array build produces empty list."""
        ctx = CodeGenContext()

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_array_build(op, ctx)

        result = self._compile_and_run(fragment.statements, {})
        output_var = fragment.bindings["term_out"]

        assert result[output_var] == []


class TestArrayBuildWithArrayInputs:
    """Tests for aBuild when inputs are arrays (concatenation, not scalar wrapping)."""

    def _compile_and_run(self, statements: list, local_vars: dict) -> dict:
        module = ast.Module(body=statements, type_ignores=[])
        ast.fix_missing_locations(module)
        code = compile(module, "<test>", "exec")
        exec(code, {}, local_vars)
        return local_vars

    def test_array_input_concatenated_not_wrapped(self):
        """Array input terminals must be concatenated with +, not wrapped in []."""
        from lvkit.models import LVType

        array_type = LVType(kind="array")
        ctx = make_ctx("term_arr", "term_out")
        ctx.bind("term_arr", "existing_list")

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term_arr", index=0, direction="input", lv_type=array_type),
                Terminal(id="term_out", index=1, direction="output"),
            ],
        )
        fragment = compound.generate_array_build(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        # Array input should NOT be wrapped: no "[existing_list]" pattern
        assert "[existing_list]" not in code
        # Should appear directly (concatenation or direct use)
        assert "existing_list" in code

        # Verify runtime: concatenation preserves elements
        result = self._compile_and_run(
            fragment.statements, {"existing_list": [1, 2, 3]}
        )
        output_var = fragment.bindings["term_out"]
        assert result[output_var] == [1, 2, 3]

    def test_scalar_input_wrapped_in_list(self):
        """Scalar (non-array) input terminals must be wrapped in []."""
        ctx = make_ctx("term_scalar", "term_out")
        ctx.bind("term_scalar", "my_val")

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                # no lv_type → treated as scalar
                Terminal(id="term_scalar", index=0, direction="input"),
                Terminal(id="term_out", index=1, direction="output"),
            ],
        )
        fragment = compound.generate_array_build(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "[my_val]" in code

        result = self._compile_and_run(
            fragment.statements, {"my_val": 42}
        )
        output_var = fragment.bindings["term_out"]
        assert result[output_var] == [42]

    def test_mixed_array_and_scalar_inputs(self):
        """Mixed array + scalar inputs: array concatenated, scalar wrapped."""
        from lvkit.models import LVType

        array_type = LVType(kind="array")
        ctx = make_ctx("term_arr", "term_scalar", "term_out")
        ctx.bind("term_arr", "head_list")
        ctx.bind("term_scalar", "new_item")

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term_arr", index=0, direction="input", lv_type=array_type),
                Terminal(id="term_scalar", index=1, direction="input"),
                Terminal(id="term_out", index=2, direction="output"),
            ],
        )
        fragment = compound.generate_array_build(op, ctx)

        result = self._compile_and_run(
            fragment.statements, {"head_list": [10, 20], "new_item": 30}
        )
        output_var = fragment.bindings["term_out"]
        assert result[output_var] == [10, 20, 30]

    def test_two_array_inputs_concatenated(self):
        """Two array inputs are concatenated directly."""
        from lvkit.models import LVType

        array_type = LVType(kind="array")
        ctx = make_ctx("term_a", "term_b", "term_out")
        ctx.bind("term_a", "list_a")
        ctx.bind("term_b", "list_b")

        op = PrimitiveOperation(
            id="build1",
            name="Build Array",
            kind="primitive",
            node_type="aBuild",
            terminals=[
                Terminal(id="term_a", index=0, direction="input", lv_type=array_type),
                Terminal(id="term_b", index=1, direction="input", lv_type=array_type),
                Terminal(id="term_out", index=2, direction="output"),
            ],
        )
        fragment = compound.generate_array_build(op, ctx)

        result = self._compile_and_run(
            fragment.statements, {"list_a": [1, 2], "list_b": [3, 4]}
        )
        output_var = fragment.bindings["term_out"]
        assert result[output_var] == [1, 2, 3, 4]
