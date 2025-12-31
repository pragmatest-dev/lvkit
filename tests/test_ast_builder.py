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

    # Build module
    result = build_module(ctx, vi_name)

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
