"""Tests for the new AST-based code generation builder."""

from __future__ import annotations

import ast

import pytest


def test_build_module_minimal():
    """Test build_module with minimal VI context."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Simple Add.vi",
        "inputs": [
            {"id": "inp:1", "name": "A", "type": "int"},
            {"id": "inp:2", "name": "B", "type": "int"},
        ],
        "outputs": [
            {"id": "out:1", "name": "Sum", "type": "int"},
        ],
        "constants": [],
        "operations": [],
        "data_flow": [],
    }

    result = build_module(vi_context, "Simple Add.vi")

    # Should be valid Python
    ast.parse(result)

    # Should have expected structure
    assert "def simple_add(" in result
    assert "class SimpleAddResult" in result
    assert "from __future__ import annotations" in result


def test_build_module_with_constant():
    """Test build_module with constants."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Constant Test.vi",
        "inputs": [],
        "outputs": [
            {"id": "out:1", "name": "Value", "type": "int"},
        ],
        "constants": [
            {"id": "const:1", "name": "MyConst", "value": 42, "type": "int"},
        ],
        "operations": [
            {
                "id": "op:1",
                "name": "Constant",
                "labels": ["Constant"],
                "terminals": [],
            }
        ],
        "data_flow": [
            {
                "src_terminal": "const:1",
                "dst_terminal": "out:1",
                "from_parent_labels": ["Constant"],
                "to_parent_labels": ["Output"],
            }
        ],
    }

    result = build_module(vi_context, "Constant Test.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def constant_test(" in result


def test_build_module_with_primitive():
    """Test build_module with a primitive operation."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Add Numbers.vi",
        "inputs": [
            {"id": "inp:1", "name": "X", "type": "float"},
            {"id": "inp:2", "name": "Y", "type": "float"},
        ],
        "outputs": [
            {"id": "out:1", "name": "Result", "type": "float"},
        ],
        "constants": [],
        "operations": [
            {
                "id": "op:1",
                "name": "Add",
                "labels": ["Primitive"],
                "primResID": 1,  # Add primitive
                "terminals": [
                    {"id": "term:1", "name": "x", "direction": "input"},
                    {"id": "term:2", "name": "y", "direction": "input"},
                    {"id": "term:3", "name": "x+y", "direction": "output"},
                ],
            }
        ],
        "data_flow": [
            {
                "src_terminal": "inp:1",
                "dst_terminal": "term:1",
                "from_parent_labels": ["Input"],
                "to_parent_labels": ["Primitive"],
            },
            {
                "src_terminal": "inp:2",
                "dst_terminal": "term:2",
                "from_parent_labels": ["Input"],
                "to_parent_labels": ["Primitive"],
            },
            {
                "src_terminal": "term:3",
                "dst_terminal": "out:1",
                "from_parent_labels": ["Primitive"],
                "to_parent_labels": ["Output"],
            },
        ],
    }

    result = build_module(vi_context, "Add Numbers.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def add_numbers(" in result


def test_build_module_with_subvi():
    """Test build_module with a SubVI call."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Call Helper.vi",
        "inputs": [
            {"id": "inp:1", "name": "Input Value", "type": "str"},
        ],
        "outputs": [
            {"id": "out:1", "name": "Output Value", "type": "str"},
        ],
        "constants": [],
        "operations": [
            {
                "id": "op:1",
                "name": "Helper VI.vi",
                "labels": ["SubVI"],
                "terminals": [
                    {"id": "term:1", "name": "input", "direction": "input"},
                    {"id": "term:2", "name": "output", "direction": "output"},
                ],
            }
        ],
        "data_flow": [
            {
                "src_terminal": "inp:1",
                "dst_terminal": "term:1",
                "from_parent_labels": ["Input"],
                "to_parent_labels": ["SubVI"],
            },
            {
                "src_terminal": "term:2",
                "dst_terminal": "out:1",
                "from_parent_labels": ["SubVI"],
                "to_parent_labels": ["Output"],
            },
        ],
    }

    result = build_module(vi_context, "Call Helper.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def call_helper(" in result
    assert "helper_vi" in result  # SubVI call


def test_code_fragment_creation():
    """Test CodeFragment creation and merging."""
    from vipy.agent.codegen import CodeFragment

    frag1 = CodeFragment(
        statements=[],
        bindings={"a": "x"},
        imports={"import foo"},
    )

    frag2 = CodeFragment(
        statements=[],
        bindings={"b": "y"},
        imports={"import bar"},
    )

    # Test empty
    empty = CodeFragment.empty()
    assert len(empty.statements) == 0
    assert len(empty.bindings) == 0


def test_context_resolution():
    """Test CodeGenContext variable resolution."""
    from vipy.agent.codegen import CodeGenContext

    ctx = CodeGenContext()
    ctx.bind("term:1", "my_var")

    # Direct binding
    assert ctx.resolve("term:1") == "my_var"

    # Unknown terminal
    assert ctx.resolve("term:unknown") is None

    # Child context
    child = ctx.child()
    child.bind("term:2", "child_var")

    # Child can resolve parent bindings
    assert child.resolve("term:1") == "my_var"
    assert child.resolve("term:2") == "child_var"


def test_context_from_vi_context():
    """Test CodeGenContext.from_vi_context initialization."""
    from vipy.agent.codegen import CodeGenContext

    vi_context = {
        "inputs": [
            {"id": "inp:1", "name": "Path In"},
            {"id": "inp:2", "name": "Count"},
        ],
        "constants": [
            {"id": "const:1", "value": 42},
        ],
        "data_flow": [
            {"src_terminal": "inp:1", "dst_terminal": "term:1"},
        ],
    }

    ctx = CodeGenContext.from_vi_context(vi_context)

    # Inputs should be bound
    assert ctx.resolve("inp:1") == "path_in"
    assert ctx.resolve("inp:2") == "count"

    # Constants should be bound
    assert ctx.resolve("const:1") is not None


def test_build_module_with_while_loop():
    """Test build_module with a while loop structure."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Loop Counter.vi",
        "inputs": [
            {"id": "inp:1", "name": "Max Count", "type": "int"},
        ],
        "outputs": [
            {"id": "out:1", "name": "Final Count", "type": "int"},
        ],
        "constants": [],
        "operations": [
            {
                "id": "loop:1",
                "name": "While Loop",
                "labels": ["Loop"],
                "loop_type": "whileLoop",
                "inner_nodes": [],
                "tunnels": [
                    {
                        "tunnel_type": "lpTun",
                        "outer_terminal_uid": "tun:outer1",
                        "inner_terminal_uid": "tun:inner1",
                    },
                    {
                        "tunnel_type": "lMax",
                        "outer_terminal_uid": "tun:outer2",
                        "inner_terminal_uid": "tun:inner2",
                    },
                ],
            }
        ],
        "data_flow": [
            {
                "src_terminal": "inp:1",
                "dst_terminal": "tun:outer1",
                "from_parent_labels": ["Input"],
                "to_parent_labels": ["Loop"],
            },
            {
                "src_terminal": "tun:outer2",
                "dst_terminal": "out:1",
                "from_parent_labels": ["Loop"],
                "to_parent_labels": ["Output"],
            },
        ],
    }

    result = build_module(vi_context, "Loop Counter.vi")

    # Should be valid Python
    ast.parse(result)

    assert "def loop_counter(" in result
    assert "while" in result


def test_build_module_with_for_loop():
    """Test build_module with a for loop structure."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Iterate Array.vi",
        "inputs": [],
        "outputs": [],
        "constants": [],
        "operations": [
            {
                "id": "loop:1",
                "name": "For Loop",
                "labels": ["Loop"],
                "loop_type": "forLoop",
                "inner_nodes": [],
                "tunnels": [],
            }
        ],
        "data_flow": [],
    }

    result = build_module(vi_context, "Iterate Array.vi")

    # Should be valid Python
    ast.parse(result)

    assert "def iterate_array(" in result
    assert "for " in result
    assert "range" in result


def test_build_module_real_vi():
    """Integration test with a real VI from samples."""
    from pathlib import Path

    from vipy.agent.codegen import build_module
    from vipy.memory_graph import InMemoryVIGraph

    vi_path = Path(
        "samples/JKI-VI-Tester/source/User Interfaces/"
        "Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi"
    )
    if not vi_path.exists():
        pytest.skip("Sample VI not available")

    graph = InMemoryVIGraph()
    graph.load_vi(vi_path, search_paths=[Path("samples/OpenG/extracted")])

    vi_name = vi_path.name
    ctx = graph.get_vi_context(vi_name)

    # Should have operations
    assert len(ctx.get("operations", [])) > 0

    # Build module (pass graph.get_vi_context for SubVI parameter resolution)
    result = build_module(ctx, vi_name, vi_context_lookup=graph.get_vi_context)

    # Should be valid Python
    ast.parse(result)

    # Should have expected structure
    assert "def get_settings_path(" in result
    assert "GetSettingsPathResult" in result
    assert "from __future__ import annotations" in result


def test_unknown_primitive_raises_at_runtime():
    """Test that unknown primitives generate NotImplementedError."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Unknown Prim.vi",
        "inputs": [],
        "outputs": [],
        "constants": [],
        "operations": [
            {
                "id": "op:1",
                "name": "Mystery Primitive",
                "labels": ["Primitive"],
                "primResID": 99999,  # Unknown primitive
                "terminals": [],
            }
        ],
        "data_flow": [],
    }

    result = build_module(vi_context, "Unknown Prim.vi")

    # Should be valid Python
    ast.parse(result)

    # Should have NotImplementedError for unknown primitive
    assert "raise NotImplementedError" in result
    assert "99999" in result


def test_unknown_node_type_emits_warning():
    """Test that unknown node types emit a warning comment."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Unknown Node.vi",
        "inputs": [],
        "outputs": [],
        "constants": [],
        "operations": [
            {
                "id": "op:1",
                "name": "Weird Node",
                "labels": ["SomethingWeird"],  # Unknown label
            }
        ],
        "data_flow": [],
    }

    result = build_module(vi_context, "Unknown Node.vi")

    # Should be valid Python
    ast.parse(result)

    # Should have warning comment
    assert "WARNING" in result or "Unknown node type" in result


# === CodeFragment Tests ===


def test_code_fragment_empty():
    """Test creating an empty CodeFragment."""
    from vipy.agent.codegen import CodeFragment

    frag = CodeFragment.empty()
    assert len(frag.statements) == 0
    assert len(frag.bindings) == 0
    assert len(frag.imports) == 0


def test_code_fragment_from_statement():
    """Test creating a CodeFragment from a single statement."""
    from vipy.agent.codegen import CodeFragment

    stmt = ast.Assign(
        targets=[ast.Name(id="x", ctx=ast.Store())],
        value=ast.Constant(value=42),
    )
    frag = CodeFragment.from_statement(stmt, {"term:1": "x"}, {"import foo"})

    assert len(frag.statements) == 1
    assert frag.bindings == {"term:1": "x"}
    assert "import foo" in frag.imports


def test_code_fragment_extend():
    """Test extending a CodeFragment with another."""
    from vipy.agent.codegen import CodeFragment

    frag1 = CodeFragment(
        statements=[],
        bindings={"a": "x"},
        imports={"import foo"},
    )
    frag2 = CodeFragment(
        statements=[],
        bindings={"b": "y"},
        imports={"import bar"},
    )

    frag1.extend(frag2)

    assert frag1.bindings == {"a": "x", "b": "y"}
    assert frag1.imports == {"import foo", "import bar"}


def test_code_fragment_add():
    """Test adding two CodeFragments."""
    from vipy.agent.codegen import CodeFragment

    frag1 = CodeFragment(bindings={"a": "x"}, imports={"import foo"})
    frag2 = CodeFragment(bindings={"b": "y"}, imports={"import bar"})

    combined = frag1 + frag2

    assert combined.bindings == {"a": "x", "b": "y"}
    assert combined.imports == {"import foo", "import bar"}
    # Original frags should be unchanged
    assert frag1.bindings == {"a": "x"}


# === CodeGenContext Additional Tests ===


def test_context_add_import():
    """Test adding imports to context."""
    from vipy.agent.codegen import CodeGenContext

    ctx = CodeGenContext()
    ctx.add_import("import os")
    ctx.add_import("from pathlib import Path")

    assert "import os" in ctx.imports
    assert "from pathlib import Path" in ctx.imports


def test_context_merge_bindings():
    """Test merging bindings into context."""
    from vipy.agent.codegen import CodeGenContext

    ctx = CodeGenContext()
    ctx.bind("t1", "x")

    ctx.merge({"t2": "y", "t3": "z"})

    assert ctx.resolve("t1") == "x"
    assert ctx.resolve("t2") == "y"
    assert ctx.resolve("t3") == "z"


def test_context_flow_map_tracing():
    """Test that context traces through data flow."""
    from vipy.agent.codegen import CodeGenContext

    data_flow = [
        {"from_terminal_id": "source", "to_terminal_id": "dest", "from_parent_id": "p1"},
    ]

    ctx = CodeGenContext(data_flow=data_flow)
    ctx.bind("source", "my_var")

    # Should trace from dest back to source
    resolved = ctx.resolve("dest")
    assert resolved == "my_var"


def test_context_cycle_detection():
    """Test that context handles cycles in data flow."""
    from vipy.agent.codegen import CodeGenContext

    # Create a cycle: a -> b -> a
    data_flow = [
        {"from_terminal_id": "a", "to_terminal_id": "b", "from_parent_id": "p1"},
        {"from_terminal_id": "b", "to_terminal_id": "a", "from_parent_id": "p2"},
    ]

    ctx = CodeGenContext(data_flow=data_flow)

    # Should not infinite loop, should return None
    result = ctx.resolve("a")
    assert result is None


def test_context_callee_param_lookup():
    """Test looking up callee parameter names."""
    from vipy.agent.codegen import CodeGenContext

    def mock_lookup(vi_name: str) -> dict | None:
        if vi_name == "Helper.vi":
            return {
                "inputs": [
                    {"slot_index": 0, "name": "Input A"},
                    {"slot_index": 1, "name": "Input B"},
                ],
                "outputs": [
                    {"slot_index": 2, "name": "Output C"},
                ],
            }
        return None

    ctx = CodeGenContext(vi_context_lookup=mock_lookup)

    assert ctx.get_callee_param_name("Helper.vi", 0) == "Input A"
    assert ctx.get_callee_param_name("Helper.vi", 1) == "Input B"
    assert ctx.get_callee_param_name("Helper.vi", 99) is None
    assert ctx.get_callee_param_name("Unknown.vi", 0) is None


def test_context_callee_output_lookup():
    """Test looking up callee output names."""
    from vipy.agent.codegen import CodeGenContext

    def mock_lookup(vi_name: str) -> dict | None:
        if vi_name == "Helper.vi":
            return {
                "inputs": [],
                "outputs": [
                    {"slot_index": 0, "name": "Result"},
                ],
            }
        return None

    ctx = CodeGenContext(vi_context_lookup=mock_lookup)

    assert ctx.get_callee_output_name("Helper.vi", 0) == "Result"
    assert ctx.get_callee_output_name("Helper.vi", 99) is None


# === DataFlowTracer Tests ===


def test_dataflow_tracer_basic():
    """Test basic DataFlowTracer functionality."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {
        "terminals": [
            {"id": "t1", "index": 0, "direction": "input", "parent_id": "op1"},
            {"id": "t2", "index": 1, "direction": "output", "parent_id": "op1"},
        ],
        "operations": [],
        "data_flow": [
            {"from_terminal_id": "source", "to_terminal_id": "t1", "from_parent_id": "input1"},
        ],
    }

    tracer = DataFlowTracer(vi_context)

    # Test is_wired
    assert tracer.is_wired("source") is True
    assert tracer.is_wired("t1") is True
    assert tracer.is_wired("unknown") is False

    # Test get_terminal
    term = tracer.get_terminal("t1")
    assert term is not None
    assert term.direction == "input"


def test_dataflow_tracer_variable_registration():
    """Test registering and retrieving variables."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {"terminals": [], "operations": [], "data_flow": []}
    tracer = DataFlowTracer(vi_context)

    tracer.register_variable("t1", "my_var")
    assert tracer.get_variable("t1") == "my_var"
    assert tracer.get_variable("unknown") is None


def test_dataflow_tracer_resolve_source():
    """Test resolving source variable for a terminal."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {
        "terminals": [
            {"id": "t1", "index": 0, "direction": "input", "parent_id": "op1"},
        ],
        "operations": [],
        "data_flow": [
            {"from_terminal_id": "source", "to_terminal_id": "t1", "from_parent_id": "input1"},
        ],
    }

    tracer = DataFlowTracer(vi_context)
    tracer.register_variable("source", "x")

    resolved = tracer.resolve_source("t1")
    assert resolved == "x"


def test_dataflow_tracer_wired_inputs():
    """Test getting wired inputs for an operation."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {
        "terminals": [
            {"id": "t1", "index": 0, "direction": "input", "parent_id": "op1"},
            {"id": "t2", "index": 1, "direction": "input", "parent_id": "op1"},
            {"id": "t3", "index": 2, "direction": "output", "parent_id": "op1"},
        ],
        "operations": [],
        "data_flow": [
            {"from_terminal_id": "src1", "to_terminal_id": "t1", "from_parent_id": "p1"},
            {"from_terminal_id": "src2", "to_terminal_id": "t2", "from_parent_id": "p2"},
        ],
    }

    tracer = DataFlowTracer(vi_context)
    tracer.register_variable("src1", "x")
    tracer.register_variable("src2", "y")

    inputs = tracer.get_wired_inputs("op1")
    assert len(inputs) == 2
    assert inputs[0] == (0, "t1", "x")
    assert inputs[1] == (1, "t2", "y")


def test_dataflow_tracer_wired_outputs():
    """Test getting wired outputs for an operation."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {
        "terminals": [
            {"id": "t1", "index": 0, "direction": "input", "parent_id": "op1"},
            {"id": "t2", "index": 1, "direction": "output", "parent_id": "op1"},
        ],
        "operations": [],
        "data_flow": [
            {"from_terminal_id": "t2", "to_terminal_id": "dest", "from_parent_id": "op1"},
        ],
    }

    tracer = DataFlowTracer(vi_context)

    outputs = tracer.get_wired_outputs("op1")
    assert len(outputs) == 1
    assert outputs[0] == (1, "t2")


# === ExpressionBuilder Tests ===


def test_expression_builder_string_hint():
    """Test building expression from string hint."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {"terminals": [], "operations": [], "data_flow": []}
    tracer = DataFlowTracer(vi_context)

    from vipy.agent.codegen.expressions import ExpressionBuilder

    builder = ExpressionBuilder(tracer)

    expr = builder.build_primitive(
        python_hint="x + y",
        input_values=["a", "b"],
        input_names=["x", "y"],
        wired_outputs=[(0, "out1", "result")],
    )

    assert expr.code == "a + b"
    assert expr.output_vars == ["result"]


def test_expression_builder_dict_hint():
    """Test building expression from dict hint."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {"terminals": [], "operations": [], "data_flow": []}
    tracer = DataFlowTracer(vi_context)

    from vipy.agent.codegen.expressions import ExpressionBuilder

    builder = ExpressionBuilder(tracer)

    expr = builder.build_primitive(
        python_hint={"sum": "x + y", "diff": "x - y"},
        input_values=["a", "b"],
        input_names=["x", "y"],
        wired_outputs=[
            (0, "out1", "sum"),
            (1, "out2", "diff"),
        ],
    )

    assert "a + b" in expr.code
    assert "a - b" in expr.code
    assert len(expr.output_vars) == 2


def test_expression_builder_subvi_call():
    """Test building SubVI call expression."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {"terminals": [], "operations": [], "data_flow": []}
    tracer = DataFlowTracer(vi_context)

    from vipy.agent.codegen.expressions import ExpressionBuilder

    builder = ExpressionBuilder(tracer)

    expr = builder.build_subvi_call(
        function_name="my_helper",
        input_values=["x", "y", "z"],
        result_var="result",
    )

    assert expr.code == "my_helper(x, y, z)"
    assert expr.output_vars == ["result"]


def test_expression_builder_with_assignment():
    """Test that assignments are stripped from hints."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {"terminals": [], "operations": [], "data_flow": []}
    tracer = DataFlowTracer(vi_context)

    from vipy.agent.codegen.expressions import ExpressionBuilder

    builder = ExpressionBuilder(tracer)

    expr = builder.build_primitive(
        python_hint="result = x + y",  # Has assignment
        input_values=["a", "b"],
        input_names=["x", "y"],
        wired_outputs=[(0, "out1", "result")],
    )

    # Assignment should be stripped
    assert expr.code == "a + b"


def test_expression_builder_with_comment():
    """Test that comments are stripped from hints."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {"terminals": [], "operations": [], "data_flow": []}
    tracer = DataFlowTracer(vi_context)

    from vipy.agent.codegen.expressions import ExpressionBuilder

    builder = ExpressionBuilder(tracer)

    expr = builder.build_primitive(
        python_hint="x + y  # Add two numbers",  # Has comment
        input_values=["a", "b"],
        input_names=["x", "y"],
        wired_outputs=[(0, "out1", "result")],
    )

    # Comment should be stripped
    assert "#" not in expr.code
    assert expr.code == "a + b"


# === Build Module Edge Cases ===


def test_build_module_with_case_structure():
    """Test build_module with a case structure."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Case Test.vi",
        "inputs": [
            {"id": "inp:1", "name": "Selector", "type": "int"},
        ],
        "outputs": [
            {"id": "out:1", "name": "Result", "type": "int"},
        ],
        "constants": [],
        "operations": [
            {
                "id": "case:1",
                "name": "Case Structure",
                "labels": ["Case"],
                "terminals": [],
            }
        ],
        "data_flow": [],
    }

    result = build_module(vi_context, "Case Test.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def case_test(" in result


def test_build_module_with_multiple_outputs():
    """Test build_module with multiple outputs."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Multi Output.vi",
        "inputs": [
            {"id": "inp:1", "name": "Input", "type": "int"},
        ],
        "outputs": [
            {"id": "out:1", "name": "Output A", "type": "int"},
            {"id": "out:2", "name": "Output B", "type": "str"},
            {"id": "out:3", "name": "Output C", "type": "float"},
        ],
        "constants": [],
        "operations": [],
        "data_flow": [],
    }

    result = build_module(vi_context, "Multi Output.vi")

    # Should be valid Python
    ast.parse(result)
    assert "class MultiOutputResult" in result
    assert "output_a" in result
    assert "output_b" in result
    assert "output_c" in result


def test_build_module_with_enum_input():
    """Test build_module with an enum input."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Enum Input.vi",
        "inputs": [
            {
                "id": "inp:1",
                "name": "Mode",
                "type": "enum",
                "enum_values": ["Read", "Write", "Append"],
            },
        ],
        "outputs": [],
        "constants": [],
        "operations": [],
        "data_flow": [],
    }

    result = build_module(vi_context, "Enum Input.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def enum_input(" in result


def test_build_module_empty_vi():
    """Test build_module with an empty VI (no inputs, outputs, or operations)."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Empty.vi",
        "inputs": [],
        "outputs": [],
        "constants": [],
        "operations": [],
        "data_flow": [],
    }

    result = build_module(vi_context, "Empty.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def empty(" in result


def test_build_module_with_nested_loops():
    """Test build_module with nested loop structures."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Nested Loops.vi",
        "inputs": [],
        "outputs": [],
        "constants": [],
        "operations": [
            {
                "id": "outer:1",
                "name": "Outer For",
                "labels": ["Loop"],
                "loop_type": "forLoop",
                "inner_nodes": [
                    {
                        "id": "inner:1",
                        "name": "Inner While",
                        "labels": ["Loop"],
                        "loop_type": "whileLoop",
                        "inner_nodes": [],
                        "tunnels": [],
                    }
                ],
                "tunnels": [],
            }
        ],
        "data_flow": [],
    }

    result = build_module(vi_context, "Nested Loops.vi")

    # Should be valid Python
    ast.parse(result)
    assert "for " in result
    assert "while " in result


def test_build_module_special_characters_in_name():
    """Test build_module handles special characters in VI name."""
    from vipy.agent.codegen import build_module

    vi_context = {
        "name": "Test-VI (Copy).vi",
        "inputs": [],
        "outputs": [],
        "constants": [],
        "operations": [],
        "data_flow": [],
    }

    result = build_module(vi_context, "Test-VI (Copy).vi")

    # Should be valid Python
    ast.parse(result)
    # Function name should be sanitized
    assert "def test_vi_copy(" in result or "def testvi_copy(" in result
