"""Tests for compound operation code generation (cpdArith, aBuild)."""

from __future__ import annotations

import ast

from lvpy.codegen.context import CodeGenContext
from lvpy.codegen.nodes import compound
from lvpy.models import PrimitiveOperation, Terminal
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
            labels=["Compound"],
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
            labels=["Compound"],
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

    def test_generate_add_multiple_inputs(self):
        """Test generating addition of multiple inputs."""
        ctx = make_ctx("term1", "term2", "term3", "term_out")
        ctx.bind("term1", "x")
        ctx.bind("term2", "y")
        ctx.bind("term3", "z")

        op = PrimitiveOperation(
            id="cpd1",
            name="Compound Add",
            labels=["Compound"],
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
            labels=["Compound"],
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
            labels=["Compound"],
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
            labels=["Compound"],
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
        from lvpy.graph.models import Wire

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
            labels=["Compound"],
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
            labels=["Compound"],
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
            labels=["Compound"],
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
            labels=["Compound"],
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
            labels=["ArrayBuild"],
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
            labels=["ArrayBuild"],
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
            labels=["ArrayBuild"],
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
            labels=["ArrayBuild"],
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
            labels=["ArrayBuild"],
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
            labels=["ArrayBuild"],
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
            labels=["ArrayBuild"],
            node_type="aBuild",
            terminals=[
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = compound.generate_array_build(op, ctx)

        result = self._compile_and_run(fragment.statements, {})
        output_var = fragment.bindings["term_out"]

        assert result[output_var] == []
