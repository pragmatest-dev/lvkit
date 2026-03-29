"""Tests for the new AST-based code generation builder."""

from __future__ import annotations

import ast

import pytest

from vipy.graph_types import (
    Constant,
    FPTerminal,
    LVType,
    Operation,
    Terminal,
    Tunnel,
    VIContext,
    Wire,
)


def test_build_module_minimal():
    """Test build_module with minimal VI context."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Simple Add.vi",
        inputs=[
            FPTerminal(
                id="inp:1",
                index=0,
                direction="input",
                name="A",
                is_indicator=False,
                is_public=True,
                lv_type=LVType(kind="primitive", underlying_type="NumInt32"),
            ),
            FPTerminal(
                id="inp:2",
                index=0,
                direction="input",
                name="B",
                is_indicator=False,
                is_public=True,
                lv_type=LVType(kind="primitive", underlying_type="NumInt32"),
            ),
        ],
        outputs=[
            FPTerminal(
                id="out:1",
                index=0,
                direction="output",
                name="Sum",
                is_indicator=True,
                is_public=True,
                lv_type=LVType(kind="primitive", underlying_type="NumInt32"),
            ),
        ],
    )

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

    vi_context = VIContext(
        name="Constant Test.vi",
        outputs=[
            FPTerminal(
                id="out:1",
                index=0,
                direction="output",
                name="Value",
                is_indicator=True,
                is_public=True,
                lv_type=LVType(kind="primitive", underlying_type="NumInt32"),
            ),
        ],
        constants=[
            Constant(id="const:1", value=42, name="MyConst"),
        ],
        operations=[
            Operation(id="op:1", name="Constant", labels=["Constant"]),
        ],
        data_flow=[
            Wire.from_terminals(
                from_terminal_id="const:1",
                to_terminal_id="out:1",
                from_parent_labels=["Constant"],
                to_parent_labels=["Output"],
            ),
        ],
    )

    result = build_module(vi_context, "Constant Test.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def constant_test(" in result


def test_build_module_with_primitive():
    """Test build_module with a primitive operation."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Add Numbers.vi",
        inputs=[
            FPTerminal(
                id="inp:1",
                index=0,
                direction="input",
                name="X",
                is_indicator=False,
                is_public=True,
                lv_type=LVType(kind="primitive", underlying_type="NumFloat64"),
            ),
            FPTerminal(
                id="inp:2",
                index=0,
                direction="input",
                name="Y",
                is_indicator=False,
                is_public=True,
                lv_type=LVType(kind="primitive", underlying_type="NumFloat64"),
            ),
        ],
        outputs=[
            FPTerminal(
                id="out:1",
                index=0,
                direction="output",
                name="Result",
                is_indicator=True,
                is_public=True,
                lv_type=LVType(kind="primitive", underlying_type="NumFloat64"),
            ),
        ],
        operations=[
            Operation(
                id="op:1",
                name="Add",
                labels=["Primitive"],
                primResID=1061,
                terminals=[
                    Terminal(id="term:1", index=1, direction="input", name="x"),
                    Terminal(id="term:2", index=2, direction="input", name="y"),
                    Terminal(id="term:3", index=0, direction="output", name="result"),
                ],
            ),
        ],
        data_flow=[
            Wire.from_terminals(
                from_terminal_id="inp:1",
                to_terminal_id="term:1",
                from_parent_labels=["Input"],
                to_parent_labels=["Primitive"],
            ),
            Wire.from_terminals(
                from_terminal_id="inp:2",
                to_terminal_id="term:2",
                from_parent_labels=["Input"],
                to_parent_labels=["Primitive"],
            ),
            Wire.from_terminals(
                from_terminal_id="term:3",
                to_terminal_id="out:1",
                from_parent_labels=["Primitive"],
                to_parent_labels=["Output"],
            ),
        ],
    )

    result = build_module(vi_context, "Add Numbers.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def add_numbers(" in result


def test_build_module_with_subvi():
    """Test build_module with a SubVI call."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Call Helper.vi",
        inputs=[
            FPTerminal(
                id="inp:1",
                index=0,
                direction="input",
                name="Input Value",
                is_indicator=False,
                is_public=True,
                lv_type=LVType(kind="primitive", underlying_type="String"),
            ),
        ],
        outputs=[
            FPTerminal(
                id="out:1",
                index=0,
                direction="output",
                name="Output Value",
                is_indicator=True,
                is_public=True,
                lv_type=LVType(kind="primitive", underlying_type="String"),
            ),
        ],
        operations=[
            Operation(
                id="op:1",
                name="Helper VI.vi",
                labels=["SubVI"],
                terminals=[
                    Terminal(id="term:1", index=0, direction="input", name="input"),
                    Terminal(id="term:2", index=1, direction="output", name="output"),
                ],
            ),
        ],
        data_flow=[
            Wire.from_terminals(
                from_terminal_id="inp:1",
                to_terminal_id="term:1",
                from_parent_labels=["Input"],
                to_parent_labels=["SubVI"],
            ),
            Wire.from_terminals(
                from_terminal_id="term:2",
                to_terminal_id="out:1",
                from_parent_labels=["SubVI"],
                to_parent_labels=["Output"],
            ),
        ],
    )

    result = build_module(vi_context, "Call Helper.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def call_helper(" in result
    assert "helper_vi" in result  # SubVI call


def test_code_fragment_creation():
    """Test CodeFragment creation and merging."""
    from vipy.agent.codegen import CodeFragment

    _frag1 = CodeFragment(
        statements=[],
        bindings={"a": "x"},
        imports={"import foo"},
    )

    _frag2 = CodeFragment(
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
    from vipy.graph_types import PrimitiveNode, Terminal
    from vipy.memory_graph import InMemoryVIGraph

    # Build a graph with terminals for bind/resolve to work
    graph = InMemoryVIGraph()
    node = PrimitiveNode(
        id="n1", vi="test.vi", name="n1",
        terminals=[
            Terminal(id="term:1", index=0, direction="output"),
            Terminal(id="term:2", index=1, direction="output"),
        ],
    )
    graph._graph.add_node("n1", node=node)
    graph._term_to_node["term:1"] = "n1"
    graph._term_to_node["term:2"] = "n1"

    ctx = CodeGenContext(graph=graph)
    ctx.bind("term:1", "my_var")

    # Direct binding
    assert ctx.resolve("term:1") == "my_var"

    # Unknown terminal
    assert ctx.resolve("term:unknown") is None

    # Child context — shares the same graph
    child = ctx.child()
    child.bind("term:2", "child_var")

    # Child can resolve parent bindings (same graph)
    assert child.resolve("term:1") == "my_var"
    assert child.resolve("term:2") == "child_var"


def test_context_from_vi_context():
    """Test CodeGenContext.from_vi_context initialization."""
    from tests.helpers import make_graph_with_terminals
    from vipy.agent.codegen import CodeGenContext

    graph = make_graph_with_terminals("inp:1", "inp:2", "const:1", "term:1")

    vi_context = VIContext(
        name="test.vi",
        inputs=[
            FPTerminal(
                id="inp:1",
                index=0,
                direction="input",
                name="Path In",
                is_indicator=False,
                is_public=True,
            ),
            FPTerminal(
                id="inp:2",
                index=0,
                direction="input",
                name="Count",
                is_indicator=False,
                is_public=True,
            ),
        ],
        constants=[
            Constant(id="const:1", value=42),
        ],
        data_flow=[
            Wire.from_terminals(from_terminal_id="inp:1", to_terminal_id="term:1"),
        ],
    )

    ctx = CodeGenContext.from_vi_context(vi_context, graph=graph)

    # Inputs should be bound
    assert ctx.resolve("inp:1") == "path_in"
    assert ctx.resolve("inp:2") == "count"

    # Constants should be bound
    assert ctx.resolve("const:1") is not None


def test_build_module_with_while_loop():
    """Test build_module with a while loop structure."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Loop Counter.vi",
        inputs=[
            FPTerminal(
                id="inp:1",
                index=0,
                direction="input",
                name="Max Count",
                is_indicator=False,
                is_public=True,
            ),
        ],
        outputs=[
            FPTerminal(
                id="out:1",
                index=0,
                direction="output",
                name="Final Count",
                is_indicator=True,
                is_public=True,
            ),
        ],
        operations=[
            Operation(
                id="loop:1",
                name="While Loop",
                labels=["Loop"],
                loop_type="whileLoop",
                tunnels=[
                    Tunnel(
                        tunnel_type="lpTun",
                        outer_terminal_uid="tun:outer1",
                        inner_terminal_uid="tun:inner1",
                    ),
                    Tunnel(
                        tunnel_type="lMax",
                        outer_terminal_uid="tun:outer2",
                        inner_terminal_uid="tun:inner2",
                    ),
                ],
            ),
        ],
        data_flow=[
            Wire.from_terminals(
                from_terminal_id="inp:1",
                to_terminal_id="tun:outer1",
                from_parent_labels=["Input"],
                to_parent_labels=["Loop"],
            ),
            Wire.from_terminals(
                from_terminal_id="tun:outer2",
                to_terminal_id="out:1",
                from_parent_labels=["Loop"],
                to_parent_labels=["Output"],
            ),
        ],
    )

    result = build_module(vi_context, "Loop Counter.vi")

    # Should be valid Python
    ast.parse(result)

    assert "def loop_counter(" in result
    assert "while" in result


def test_build_module_with_for_loop():
    """Test build_module with a for loop structure."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Iterate Array.vi",
        operations=[
            Operation(
                id="loop:1",
                name="For Loop",
                labels=["Loop"],
                loop_type="forLoop",
            ),
        ],
    )

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
    assert len(ctx.operations) > 0

    # Build module (terminal names are now on Terminal objects directly)
    result = build_module(ctx, vi_name)

    # Should be valid Python
    ast.parse(result)

    # Should have expected structure
    assert "def get_settings_path(" in result
    assert "GetSettingsPathResult" in result
    assert "from __future__ import annotations" in result


def test_unknown_primitive_raises_at_runtime():
    """Test that unknown primitives raise PrimitiveResolutionNeeded."""
    from vipy.agent.codegen import build_module
    from vipy.primitive_resolver import PrimitiveResolutionNeeded

    vi_context = VIContext(
        name="Unknown Prim.vi",
        operations=[
            Operation(
                id="op:1",
                name="Mystery Primitive",
                labels=["Primitive"],
                primResID=99999,
            ),
        ],
    )

    with pytest.raises(PrimitiveResolutionNeeded) as exc_info:
        build_module(vi_context, "Unknown Prim.vi")

    assert "99999" in str(exc_info.value)


def test_unknown_node_type_emits_warning():
    """Test that unknown node types emit a warning comment."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Unknown Node.vi",
        operations=[
            Operation(id="op:1", name="Weird Node", labels=["SomethingWeird"]),
        ],
    )

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
    from tests.helpers import make_ctx

    ctx = make_ctx("t1", "t2", "t3")
    ctx.bind("t1", "x")

    ctx.merge({"t2": "y", "t3": "z"})

    assert ctx.resolve("t1") == "x"
    assert ctx.resolve("t2") == "y"
    assert ctx.resolve("t3") == "z"


def test_context_flow_map_tracing():
    """Test that context traces through data flow via graph."""
    from tests.helpers import make_graph_with_edge
    from vipy.agent.codegen import CodeGenContext

    graph = make_graph_with_edge("source", "dest")

    ctx = CodeGenContext(graph=graph)
    ctx.bind("source", "my_var")

    resolved = ctx.resolve("dest")
    assert resolved == "my_var"


def test_context_cycle_detection():
    """Test that context handles cycles in data flow."""
    from vipy.agent.codegen import CodeGenContext
    from vipy.graph_types import WireEnd
    from vipy.memory_graph import InMemoryVIGraph

    graph = InMemoryVIGraph()
    graph._graph.add_node("p1", node=None)
    graph._graph.add_node("p2", node=None)
    graph._graph.add_edge("p1", "p2",
        source=WireEnd(terminal_id="a", node_id="p1"),
        dest=WireEnd(terminal_id="b", node_id="p2"))
    graph._graph.add_edge("p2", "p1",
        source=WireEnd(terminal_id="b", node_id="p2"),
        dest=WireEnd(terminal_id="a", node_id="p1"))
    graph._term_to_node["a"] = "p1"
    graph._term_to_node["b"] = "p2"

    ctx = CodeGenContext(graph=graph)
    result = ctx.resolve("a")
    assert result is None


def test_context_callee_lookup_removed():
    """Verify callee lookup methods have been removed from CodeGenContext.

    Terminal names are now populated directly on Terminal objects
    via callee_param_name, so the context no longer needs these lookups.
    """
    from vipy.agent.codegen import CodeGenContext

    ctx = CodeGenContext()
    assert not hasattr(ctx, "get_callee_param_name")
    assert not hasattr(ctx, "get_callee_output_name")
    assert not hasattr(ctx, "vi_context_lookup")


# === DataFlowTracer Tests ===


def test_dataflow_tracer_basic():
    """Test basic DataFlowTracer functionality."""
    from vipy.agent.codegen import DataFlowTracer

    vi_context = {
        "terminals": [
            Terminal(id="t1", index=0, direction="input"),
            Terminal(id="t2", index=1, direction="output"),
        ],
        "operations": [],
        "data_flow": [
            Wire.from_terminals(
                from_terminal_id="source",
                to_terminal_id="t1",
                from_parent_id="input1",
            ),
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
            Terminal(id="t1", index=0, direction="input"),
        ],
        "operations": [],
        "data_flow": [
            Wire.from_terminals(
                from_terminal_id="source",
                to_terminal_id="t1",
                from_parent_id="input1",
            ),
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
        "terminals": [],
        "operations": [
            Operation(
                id="op1",
                name="Test Op",
                labels=["Operation"],
                terminals=[
                    Terminal(id="t1", index=0, direction="input"),
                    Terminal(id="t2", index=1, direction="input"),
                    Terminal(id="t3", index=2, direction="output"),
                ],
            ),
        ],
        "data_flow": [
            Wire.from_terminals(
                from_terminal_id="src1",
                to_terminal_id="t1",
                from_parent_id="p1",
                to_parent_id="op1",
            ),
            Wire.from_terminals(
                from_terminal_id="src2",
                to_terminal_id="t2",
                from_parent_id="p2",
                to_parent_id="op1",
            ),
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
        "terminals": [],
        "operations": [
            Operation(
                id="op1",
                name="Test Op",
                labels=["Operation"],
                terminals=[
                    Terminal(id="t1", index=0, direction="input"),
                    Terminal(id="t2", index=1, direction="output"),
                ],
            ),
        ],
        "data_flow": [
            Wire.from_terminals(
                from_terminal_id="t2",
                to_terminal_id="dest",
                from_parent_id="op1",
            ),
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

    vi_context = VIContext(
        name="Case Test.vi",
        inputs=[
            FPTerminal(
                id="inp:1",
                index=0,
                direction="input",
                name="Selector",
                is_indicator=False,
                is_public=True,
            ),
        ],
        outputs=[
            FPTerminal(
                id="out:1",
                index=0,
                direction="output",
                name="Result",
                is_indicator=True,
                is_public=True,
            ),
        ],
        operations=[
            Operation(id="case:1", name="Case Structure", labels=["Case"]),
        ],
    )

    result = build_module(vi_context, "Case Test.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def case_test(" in result


def test_build_module_with_multiple_outputs():
    """Test build_module with multiple outputs."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Multi Output.vi",
        inputs=[
            FPTerminal(
                id="inp:1",
                index=0,
                direction="input",
                name="Input",
                is_indicator=False,
                is_public=True,
            ),
        ],
        outputs=[
            FPTerminal(
                id="out:1",
                index=0,
                direction="output",
                name="Output A",
                is_indicator=True,
                is_public=True,
            ),
            FPTerminal(
                id="out:2",
                index=0,
                direction="output",
                name="Output B",
                is_indicator=True,
                is_public=True,
            ),
            FPTerminal(
                id="out:3",
                index=0,
                direction="output",
                name="Output C",
                is_indicator=True,
                is_public=True,
            ),
        ],
    )

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

    vi_context = VIContext(
        name="Enum Input.vi",
        inputs=[
            FPTerminal(
                id="inp:1",
                index=0,
                direction="input",
                name="Mode",
                is_indicator=False,
                is_public=True,
                type="enum",
                enum_values=["Read", "Write", "Append"],
            ),
        ],
    )

    result = build_module(vi_context, "Enum Input.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def enum_input(" in result


def test_build_module_empty_vi():
    """Test build_module with an empty VI (no inputs, outputs, or operations)."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Empty.vi",
    )

    result = build_module(vi_context, "Empty.vi")

    # Should be valid Python
    ast.parse(result)
    assert "def empty(" in result


def test_build_module_with_nested_loops():
    """Test build_module with nested loop structures."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Nested Loops.vi",
        operations=[
            Operation(
                id="outer:1",
                name="Outer For",
                labels=["Loop"],
                loop_type="forLoop",
                inner_nodes=[
                    Operation(
                        id="inner:1",
                        name="Inner While",
                        labels=["Loop"],
                        loop_type="whileLoop",
                    ),
                ],
            ),
        ],
    )

    result = build_module(vi_context, "Nested Loops.vi")

    # Should be valid Python
    ast.parse(result)
    assert "for " in result
    assert "while " in result


def test_build_module_special_characters_in_name():
    """Test build_module handles special characters in VI name."""
    from vipy.agent.codegen import build_module

    vi_context = VIContext(
        name="Test-VI (Copy).vi",
    )

    result = build_module(vi_context, "Test-VI (Copy).vi")

    # Should be valid Python
    ast.parse(result)
    # Function name should be sanitized
    assert "def test_vi_copy(" in result or "def testvi_copy(" in result


# === LVType Tests ===


class TestLVTypeToPython:
    """Tests for LVType.to_python() type annotation generation."""

    def test_primitive_int_types(self):
        """Test primitive integer types map to int."""
        from vipy.graph_types import LVType

        for int_type in ["NumInt8", "NumInt16", "NumInt32", "NumInt64",
                         "NumUInt8", "NumUInt16", "NumUInt32", "NumUInt64"]:
            lv_type = LVType(kind="primitive", underlying_type=int_type)
            assert lv_type.to_python() == "int"

    def test_primitive_float_types(self):
        """Test primitive float types map to float."""
        from vipy.graph_types import LVType

        for float_type in ["NumFloat32", "NumFloat64"]:
            lv_type = LVType(kind="primitive", underlying_type=float_type)
            assert lv_type.to_python() == "float"

    def test_primitive_string(self):
        """Test String maps to str."""
        from vipy.graph_types import LVType

        lv_type = LVType(kind="primitive", underlying_type="String")
        assert lv_type.to_python() == "str"

    def test_primitive_boolean(self):
        """Test Boolean maps to bool."""
        from vipy.graph_types import LVType

        lv_type = LVType(kind="primitive", underlying_type="Boolean")
        assert lv_type.to_python() == "bool"

    def test_primitive_path(self):
        """Test Path maps to Path."""
        from vipy.graph_types import LVType

        lv_type = LVType(kind="primitive", underlying_type="Path")
        assert lv_type.to_python() == "Path"

    def test_primitive_variant(self):
        """Test Variant maps to Any."""
        from vipy.graph_types import LVType

        lv_type = LVType(kind="primitive", underlying_type="Variant")
        assert lv_type.to_python() == "Any"

    def test_primitive_void(self):
        """Test Void maps to None."""
        from vipy.graph_types import LVType

        lv_type = LVType(kind="primitive", underlying_type="Void")
        assert lv_type.to_python() == "None"

    def test_primitive_unknown(self):
        """Test unknown primitive type maps to Any."""
        from vipy.graph_types import LVType

        lv_type = LVType(kind="primitive", underlying_type="UnknownType")
        assert lv_type.to_python() == "Any"

    def test_array_1d(self):
        """Test 1D array type annotation."""
        from vipy.graph_types import LVType

        element = LVType(kind="primitive", underlying_type="NumInt32")
        arr = LVType(kind="array", element_type=element, dimensions=1)
        assert arr.to_python() == "list[int]"

    def test_array_2d(self):
        """Test 2D array type annotation."""
        from vipy.graph_types import LVType

        element = LVType(kind="primitive", underlying_type="NumFloat64")
        arr = LVType(kind="array", element_type=element, dimensions=2)
        assert arr.to_python() == "list[list[float]]"

    def test_array_no_element_type(self):
        """Test array with no element type defaults to Any."""
        from vipy.graph_types import LVType

        arr = LVType(kind="array")
        assert arr.to_python() == "list[Any]"

    def test_cluster_with_typedef_name(self):
        """Test cluster with typedef name uses class name."""
        from vipy.graph_types import LVType

        cluster = LVType(
            kind="cluster",
            typedef_name="error.ctl:Error Cluster.ctl"
        )
        assert cluster.to_python() == "ErrorCluster"

    def test_cluster_without_typedef_name(self):
        """Test cluster without typedef name uses generic dict."""
        from vipy.graph_types import LVType

        cluster = LVType(kind="cluster")
        assert cluster.to_python() == "dict[str, Any]"

    def test_enum_with_typedef_name(self):
        """Test enum with typedef name uses class name."""
        from vipy.graph_types import LVType

        enum = LVType(
            kind="enum",
            typedef_name="lib:FileMode.ctl"
        )
        assert enum.to_python() == "FileMode"

    def test_enum_without_typedef_name(self):
        """Test enum without typedef name uses int."""
        from vipy.graph_types import LVType

        enum = LVType(kind="enum")
        assert enum.to_python() == "int"

    def test_ring_with_typedef_name(self):
        """Test ring with typedef name uses class name."""
        from vipy.graph_types import LVType

        ring = LVType(
            kind="ring",
            typedef_name="option.ctl:OptionRing.ctl"
        )
        assert ring.to_python() == "OptionRing"

    def test_ring_without_typedef_name(self):
        """Test ring without typedef name uses int."""
        from vipy.graph_types import LVType

        ring = LVType(kind="ring")
        assert ring.to_python() == "int"

    def test_typedef_ref_with_name(self):
        """Test typedef_ref with name uses class name."""
        from vipy.graph_types import LVType

        # typedef_name uses ":" format like other typedef names
        ref = LVType(
            kind="typedef_ref",
            typedef_name="vi.lib/Utility:TypeDef.ctl"
        )
        assert ref.to_python() == "TypeDef"

    def test_typedef_ref_without_name(self):
        """Test typedef_ref without name uses Any."""
        from vipy.graph_types import LVType

        ref = LVType(kind="typedef_ref")
        assert ref.to_python() == "Any"

    def test_unknown_kind(self):
        """Test unknown kind returns Any."""
        from vipy.graph_types import LVType

        lv_type = LVType(kind="unknown_kind")
        assert lv_type.to_python() == "Any"


class TestWireSlotIndex:
    """Tests for Wire slot_index fields."""

    def test_wire_from_slot_index_stored(self):
        """Test that from_slot_index is stored on Wire."""
        wire = Wire.from_terminals(
            from_terminal_id="src",
            to_terminal_id="dest",
            from_slot_index=3,
        )
        assert wire.from_slot_index == 3

    def test_wire_to_slot_index_stored(self):
        """Test that to_slot_index is stored on Wire."""
        wire = Wire.from_terminals(
            from_terminal_id="src",
            to_terminal_id="dest",
            to_slot_index=5,
        )
        assert wire.to_slot_index == 5

    def test_wire_slot_indices_default_none(self):
        """Test that slot indices default to None."""
        wire = Wire.from_terminals(
            from_terminal_id="src",
            to_terminal_id="dest",
        )
        assert wire.from_slot_index is None
        assert wire.to_slot_index is None

    def test_wire_with_both_slot_indices(self):
        """Test Wire with both slot indices set."""
        wire = Wire.from_terminals(
            from_terminal_id="src",
            to_terminal_id="dest",
            from_slot_index=0,
            to_slot_index=2,
        )
        assert wire.from_slot_index == 0
        assert wire.to_slot_index == 2

    def test_wire_slot_index_in_graph_query(self):
        """Test that slot indices are accessible via graph edge queries."""
        from vipy.agent.codegen.context import CodeGenContext
        from vipy.graph_types import WireEnd
        from vipy.memory_graph import InMemoryVIGraph

        graph = InMemoryVIGraph()
        graph._graph.add_node("p1", node=None)
        graph._graph.add_node("p2", node=None)
        src = WireEnd(terminal_id="src", node_id="p1", index=1)
        dst = WireEnd(terminal_id="dest", node_id="p2", index=3)
        graph._graph.add_edge("p1", "p2", source=src, dest=dst)
        graph._term_to_node["src"] = "p1"
        graph._term_to_node["dest"] = "p2"

        ctx = CodeGenContext(graph=graph)
        source_info = ctx.get_source("dest")
        assert source_info is not None
        assert source_info.src_slot_index == 1


# === to_var_name Tests ===


class TestToVarName:
    """Tests for to_var_name variable name conversion."""

    # Note: Null character handling was removed from to_var_name.
    # Labels are now properly extracted using partID=16 in the parser,
    # so null characters should never reach to_var_name.

    def test_empty_string(self):
        """Test that empty string becomes 'var'."""
        from vipy.agent.codegen.ast_utils import to_var_name

        assert to_var_name("") == "var"

    def test_none_equivalent(self):
        """Test that None-ish values become 'var'."""
        from vipy.agent.codegen.ast_utils import to_var_name

        assert to_var_name(None) == "var"  # type: ignore

    def test_normal_name(self):
        """Test that normal names are converted correctly."""
        from vipy.agent.codegen.ast_utils import to_var_name

        assert to_var_name("Input Value") == "input_value"
        assert to_var_name("error in") == "error_in"
        assert to_var_name("My-Variable") == "my_variable"

    def test_keyword_escaping(self):
        """Test that Python keywords get underscore suffix."""
        from vipy.agent.codegen.ast_utils import to_var_name

        assert to_var_name("pass") == "pass_"
        assert to_var_name("class") == "class_"
        assert to_var_name("for") == "for_"

    def test_numeric_prefix(self):
        """Test that names starting with numbers get 'var_' prefix."""
        from vipy.agent.codegen.ast_utils import to_var_name

        assert to_var_name("123abc") == "var_123abc"
        assert to_var_name("1st value") == "var_1st_value"


# === Error Cluster Filtering Tests ===


class TestErrorClusterFiltering:
    """Tests for error cluster filtering in code generation."""

    def test_error_cluster_input_filtered_by_name(self):
        """Test that error cluster inputs are filtered by name pattern."""
        from vipy.agent.codegen.builder import build_args
        from vipy.graph_types import FPTerminal, Terminal

        inputs = [
            Terminal(
                id="1",
                index=0,
                direction="input",
                name="error in (no error)",
                is_indicator=False,
                is_public=True,
            ),
            FPTerminal(
                id="2",
                index=0,
                direction="input",
                name="value",
                is_indicator=False,
                is_public=True,
            ),
            FPTerminal(
                id="3",
                index=0,
                direction="input",
                name="error out",
                is_indicator=False,
                is_public=True,
            ),
        ]

        args = build_args(inputs)

        # Should only have "value" - error in/out filtered
        assert len(args.args) == 1
        assert args.args[0].arg == "value"

    def test_error_cluster_output_filtered_by_name(self):
        """Test that error cluster outputs are filtered by name pattern."""
        from vipy.agent.codegen.builder import build_result_class
        from vipy.graph_types import FPTerminal

        vi_context = VIContext(
            name="Test.vi",
            outputs=[
                FPTerminal(
                    id="1",
                    index=0,
                    direction="output",
                    name="error out",
                    is_indicator=True,
                    is_public=True,
                ),
                FPTerminal(
                    id="2",
                    index=0,
                    direction="output",
                    name="result",
                    is_indicator=True,
                    is_public=True,
                ),
            ],
        )

        result_class = build_result_class(vi_context)

        # Should only have "result" field - error out filtered
        assert result_class is not None
        # Check class body has only one field annotation
        field_names = [
            stmt.target.id
            for stmt in result_class.body
            if hasattr(stmt, "target")
        ]
        assert field_names == ["result"]

    def test_all_error_outputs_returns_none(self):
        """Test that if all outputs are error clusters, no result class is created."""
        from vipy.agent.codegen.builder import build_result_class
        from vipy.graph_types import FPTerminal

        vi_context = VIContext(
            name="Test.vi",
            outputs=[
                FPTerminal(
                    id="1",
                    index=0,
                    direction="output",
                    name="error out",
                    is_indicator=True,
                    is_public=True,
                ),
            ],
        )

        result_class = build_result_class(vi_context)

        # Should be None - no non-error outputs
        assert result_class is None
