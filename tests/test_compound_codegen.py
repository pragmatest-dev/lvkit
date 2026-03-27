"""Tests for compound operation code generation (cpdArith, aBuild)."""

from __future__ import annotations

import ast

import pytest

from tests.helpers import make_ctx
from vipy.agent.codegen.context import CodeGenContext
from vipy.agent.codegen.nodes.compound import ArrayBuildCodeGen, CompoundArithCodeGen
from vipy.graph_types import Operation, Terminal


class TestCompoundArithMakeVarName:
    """Tests for CompoundArithCodeGen._make_var_name()."""

    @pytest.fixture
    def codegen(self) -> CompoundArithCodeGen:
        return CompoundArithCodeGen()

    def test_make_var_name_boolean_or_returns_should_stop(
        self, codegen: CompoundArithCodeGen
    ):
        """Test that boolean OR operation returns 'should_stop'."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name("or", [], ctx)
        assert var_name == "should_stop"

    def test_make_var_name_boolean_and_returns_should_stop(
        self, codegen: CompoundArithCodeGen
    ):
        """Test that boolean AND operation returns 'should_stop'."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name("and", [], ctx)
        assert var_name == "should_stop"

    def test_make_var_name_with_stop_keyword_input(
        self, codegen: CompoundArithCodeGen
    ):
        """Test detection of stop-related keywords in input names."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name("or", ["user_stopped", "timeout"], ctx)
        assert var_name == "should_stop"

    def test_make_var_name_with_done_keyword_input(
        self, codegen: CompoundArithCodeGen
    ):
        """Test detection of done-related keywords in input names."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name("or", ["is_done", "other_flag"], ctx)
        assert var_name == "should_stop"

    def test_make_var_name_add_returns_total(self, codegen: CompoundArithCodeGen):
        """Test that add operation returns 'total'."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name("add", ["x", "y", "z"], ctx)
        assert var_name == "total"

    def test_make_var_name_unknown_returns_combined(
        self, codegen: CompoundArithCodeGen
    ):
        """Test that unknown operation returns 'combined'."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name("multiply", [], ctx)
        assert var_name == "combined"


class TestCompoundArithGenerate:
    """Tests for CompoundArithCodeGen.generate()."""

    @pytest.fixture
    def codegen(self) -> CompoundArithCodeGen:
        return CompoundArithCodeGen()

    def test_generate_or_two_inputs(self, codegen: CompoundArithCodeGen):
        """Test generating OR of two inputs."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "flag_a")
        ctx.bind("term2", "flag_b")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        assert len(fragment.statements) == 1
        assert "term_out" in fragment.bindings

        # Should produce: should_stop = flag_a or flag_b
        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "should_stop" in code
        assert "flag_a" in code
        assert "flag_b" in code
        assert " or " in code

    def test_generate_and_two_inputs(self, codegen: CompoundArithCodeGen):
        """Test generating AND of two inputs."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "cond_a")
        ctx.bind("term2", "cond_b")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert " and " in code

    def test_generate_add_multiple_inputs(self, codegen: CompoundArithCodeGen):
        """Test generating addition of multiple inputs."""
        ctx = make_ctx("term1", "term2", "term3", "term_out")
        ctx.bind("term1", "x")
        ctx.bind("term2", "y")
        ctx.bind("term3", "z")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "total" in code
        assert "+" in code

    def test_generate_single_input_passthrough(self, codegen: CompoundArithCodeGen):
        """Test that single input is passed through without assignment."""
        ctx = make_ctx("term1", "term_out")
        ctx.bind("term1", "only_value")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        # Single input should passthrough, no statements needed
        assert len(fragment.statements) == 0
        assert fragment.bindings["term_out"] == "only_value"

    def test_generate_no_inputs_default_value(self, codegen: CompoundArithCodeGen):
        """Test that no inputs produces default value."""
        ctx = CodeGenContext()

        op = Operation(
            id="cpd1",
            name="Compound Or",
            labels=["Compound"],
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = codegen.generate(op, ctx)

        # Should produce assignment to False for boolean operation
        assert len(fragment.statements) == 1
        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "False" in code

    def test_generate_no_output_returns_empty(self, codegen: CompoundArithCodeGen):
        """Test that no output terminal returns empty fragment."""
        ctx = CodeGenContext()

        op = Operation(
            id="cpd1",
            name="Compound Or",
            labels=["Compound"],
            node_type="cpdArith",
            operation="or",
            terminals=[
                Terminal(id="term1", index=1, direction="input"),
            ],
        )

        fragment = codegen.generate(op, ctx)

        assert len(fragment.statements) == 0
        assert len(fragment.bindings) == 0


class TestCompoundArithResolvesThroughDataflow:
    """Tests verifying compound ops resolve inputs through dataflow."""

    @pytest.fixture
    def codegen(self) -> CompoundArithCodeGen:
        return CompoundArithCodeGen()

    def test_or_resolves_through_wires(self, codegen: CompoundArithCodeGen):
        """Test that OR resolves inputs through wire connections."""
        from vipy.graph_types import Wire

        # Set up dataflow: src1 -> term1, src2 -> term2
        data_flow = [
            Wire.from_terminals(from_terminal_id="src1", to_terminal_id="term1"),
            Wire.from_terminals(from_terminal_id="src2", to_terminal_id="term2"),
        ]
        ctx = CodeGenContext.from_wires(data_flow)
        # Bind at SOURCE terminals, not the cpd input terminals
        ctx.bind("src1", "error_in.status")
        ctx.bind("src2", "timeout_occurred")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        # Should resolve through wires to find source values
        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "error_in.status" in code, f"Should resolve src1, got: {code}"
        assert "timeout_occurred" in code, f"Should resolve src2, got: {code}"


class TestCompoundArithExecutable:
    """Tests that verify generated compound arithmetic code executes correctly."""

    @pytest.fixture
    def codegen(self) -> CompoundArithCodeGen:
        return CompoundArithCodeGen()

    def _compile_and_run(self, statements: list, local_vars: dict) -> dict:
        """Compile statements and execute, returning resulting locals."""
        module = ast.Module(body=statements, type_ignores=[])
        ast.fix_missing_locations(module)
        code = compile(module, "<test>", "exec")
        exec(code, {}, local_vars)
        return local_vars

    def test_or_evaluates_correctly(self, codegen: CompoundArithCodeGen):
        """Test that generated OR code evaluates correctly at runtime."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "flag_a")
        ctx.bind("term2", "flag_b")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

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

    def test_and_evaluates_correctly(self, codegen: CompoundArithCodeGen):
        """Test that generated AND code evaluates correctly at runtime."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "cond_a")
        ctx.bind("term2", "cond_b")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

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

    def test_add_evaluates_correctly(self, codegen: CompoundArithCodeGen):
        """Test that generated ADD code evaluates correctly at runtime."""
        ctx = make_ctx("term1", "term2", "term3", "term_out")
        ctx.bind("term1", "a")
        ctx.bind("term2", "b")
        ctx.bind("term3", "c")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        # Test: 1 + 2 + 3 = 6
        result = self._compile_and_run(
            fragment.statements, {"a": 1, "b": 2, "c": 3}
        )
        output_var = fragment.bindings["term_out"]
        assert result[output_var] == 6


class TestArrayBuildMakeVarName:
    """Tests for ArrayBuildCodeGen._make_var_name()."""

    @pytest.fixture
    def codegen(self) -> ArrayBuildCodeGen:
        return ArrayBuildCodeGen()

    def test_make_var_name_no_inputs(self, codegen: ArrayBuildCodeGen):
        """Test default name when no inputs."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name([], ctx)
        assert var_name == "items"

    def test_make_var_name_common_base_pluralized(self, codegen: ArrayBuildCodeGen):
        """Test pluralizing common base name."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name(["path_part_1", "path_part_2"], ctx)
        # Should detect "path_part" as common base and pluralize
        assert "path" in var_name

    def test_make_var_name_fallback_items(self, codegen: ArrayBuildCodeGen):
        """Test fallback to 'items' when no common base."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name(["x", "y", "z"], ctx)
        assert var_name == "items"

    def test_make_var_name_pluralize_y_to_ies(self, codegen: ArrayBuildCodeGen):
        """Test pluralizing words ending in y to ies."""
        ctx = CodeGenContext()
        # If base is "entry", should become "entries"
        var_name = codegen._make_var_name(["entry_1", "entry_2", "entry_3"], ctx)
        assert var_name == "entries"

    def test_make_var_name_pluralize_s_ending(self, codegen: ArrayBuildCodeGen):
        """Test pluralizing words ending in s/x/z/ch/sh."""
        ctx = CodeGenContext()
        var_name = codegen._make_var_name(["box_1", "box_2"], ctx)
        assert var_name == "boxes"


class TestArrayBuildGenerate:
    """Tests for ArrayBuildCodeGen.generate()."""

    @pytest.fixture
    def codegen(self) -> ArrayBuildCodeGen:
        return ArrayBuildCodeGen()

    def test_generate_builds_list(self, codegen: ArrayBuildCodeGen):
        """Test that aBuild generates a list."""
        ctx = make_ctx("term1", "term2", "term3", "term_out")
        ctx.bind("term1", "val1")
        ctx.bind("term2", "val2")
        ctx.bind("term3", "val3")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        assert len(fragment.statements) == 1
        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "[" in code
        assert "val1" in code
        assert "val2" in code
        assert "val3" in code

    def test_generate_handles_missing_inputs(self, codegen: ArrayBuildCodeGen):
        """Test that missing inputs become None placeholders."""
        ctx = make_ctx("term1", "term2", "term_out")
        ctx.bind("term1", "val1")
        # term2 is not bound

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "None" in code

    def test_generate_no_output_returns_empty(self, codegen: ArrayBuildCodeGen):
        """Test that no output terminal returns empty fragment."""
        ctx = CodeGenContext()

        op = Operation(
            id="build1",
            name="Build Array",
            labels=["ArrayBuild"],
            node_type="aBuild",
            terminals=[
                Terminal(id="term1", index=0, direction="input"),
            ],
        )

        fragment = codegen.generate(op, ctx)

        assert len(fragment.statements) == 0
        assert len(fragment.bindings) == 0

    def test_generate_empty_array(self, codegen: ArrayBuildCodeGen):
        """Test generating empty array when no inputs."""
        ctx = CodeGenContext()

        op = Operation(
            id="build1",
            name="Build Array",
            labels=["ArrayBuild"],
            node_type="aBuild",
            terminals=[
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = codegen.generate(op, ctx)

        ast.fix_missing_locations(fragment.statements[0])
        code = ast.unparse(fragment.statements[0])
        assert "[]" in code or "items = []" in code


class TestArrayBuildExecutable:
    """Tests that verify generated array build code executes correctly."""

    @pytest.fixture
    def codegen(self) -> ArrayBuildCodeGen:
        return ArrayBuildCodeGen()

    def _compile_and_run(self, statements: list, local_vars: dict) -> dict:
        """Compile statements and execute, returning resulting locals."""
        module = ast.Module(body=statements, type_ignores=[])
        ast.fix_missing_locations(module)
        code = compile(module, "<test>", "exec")
        exec(code, {}, local_vars)
        return local_vars

    def test_build_array_produces_correct_list(self, codegen: ArrayBuildCodeGen):
        """Test that generated array build produces correct list at runtime."""
        ctx = make_ctx("term1", "term2", "term3", "term_out")
        ctx.bind("term1", "first")
        ctx.bind("term2", "second")
        ctx.bind("term3", "third")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        # Execute with test data
        result = self._compile_and_run(
            fragment.statements, {"first": "a", "second": "b", "third": "c"}
        )
        output_var = fragment.bindings["term_out"]

        # Should produce ["a", "b", "c"]
        assert output_var in result
        assert result[output_var] == ["a", "b", "c"]

    def test_build_array_preserves_order(self, codegen: ArrayBuildCodeGen):
        """Test that array build preserves input order based on terminal index."""
        ctx = make_ctx("term_0", "term_1", "term_2", "term_out")
        ctx.bind("term_0", "zero")
        ctx.bind("term_1", "one")
        ctx.bind("term_2", "two")

        op = Operation(
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

        fragment = codegen.generate(op, ctx)

        result = self._compile_and_run(
            fragment.statements, {"zero": 0, "one": 1, "two": 2}
        )
        output_var = fragment.bindings["term_out"]

        # Should be ordered by index: [0, 1, 2]
        assert result[output_var] == [0, 1, 2]

    def test_build_empty_array_produces_empty_list(self, codegen: ArrayBuildCodeGen):
        """Test that empty array build produces empty list."""
        ctx = CodeGenContext()

        op = Operation(
            id="build1",
            name="Build Array",
            labels=["ArrayBuild"],
            node_type="aBuild",
            terminals=[
                Terminal(id="term_out", index=0, direction="output"),
            ],
        )

        fragment = codegen.generate(op, ctx)

        result = self._compile_and_run(fragment.statements, {})
        output_var = fragment.bindings["term_out"]

        assert result[output_var] == []
