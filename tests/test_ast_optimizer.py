"""Tests for AST optimization passes."""

import ast

from lvpy.agent.codegen.ast_optimizer import (
    DeadCodeEliminator,
    eliminate_dead_code,
    remove_unused_imports,
)


def test_eliminate_unused_assignment():
    """Test that unused variable assignments are removed."""
    code = """
def test_func():
    x = 1
    y = 2  # Dead - never used
    return x
"""
    module = ast.parse(code)
    optimized = eliminate_dead_code(module)
    result = ast.unparse(optimized)

    assert "y = 2" not in result
    assert "x = 1" in result
    assert "return x" in result


def test_keep_used_variables():
    """Test that used variables are kept."""
    code = """
def test_func():
    x = 1
    y = x + 1
    return y
"""
    module = ast.parse(code)
    optimized = eliminate_dead_code(module)
    result = ast.unparse(optimized)

    assert "x = 1" in result
    assert "y = x + 1" in result
    assert "return y" in result


def test_eliminate_multiple_dead_assignments():
    """Test elimination of multiple unused assignments."""
    code = """
def test_func():
    used = 1
    dead1 = 2
    dead2 = 3
    dead3 = 4
    return used
"""
    module = ast.parse(code)
    optimized = eliminate_dead_code(module)
    result = ast.unparse(optimized)

    assert "used = 1" in result
    assert "dead1" not in result
    assert "dead2" not in result
    assert "dead3" not in result


def test_eliminate_inlined_subvi_dead_outputs():
    """Test elimination of dead outputs from inlined SubVIs.

    This is the primary use case - when a SubVI is inlined, its output
    variables may not be consumed by downstream code.
    """
    code = """
def get_settings_path():
    result = get_system_directory(7)
    appended_path = result.path / 'Settings.ini'
    stripped_path = appended_path.parent
    name = appended_path.name  # Dead - never used
    create_dir(stripped_path)
    create_dir_dup_directory_path = stripped_path  # Dead - inlined output
    create_dir_created_directories = []  # Dead - inlined output
    return appended_path
"""
    module = ast.parse(code)
    optimized = eliminate_dead_code(module)
    result = ast.unparse(optimized)

    # Keep used variables
    assert "result = get_system_directory" in result
    assert "appended_path = result.path" in result
    assert "stripped_path = appended_path.parent" in result
    assert "create_dir(stripped_path)" in result
    assert "return appended_path" in result

    # Remove dead variables
    assert "name = appended_path.name" not in result
    assert "create_dir_dup_directory_path" not in result
    assert "create_dir_created_directories" not in result


def test_attribute_reference_counts_as_usage():
    """Test that accessing attributes counts as variable usage."""
    code = """
def test_func():
    obj = get_object()
    value = obj.field
    return value
"""
    module = ast.parse(code)
    optimized = eliminate_dead_code(module)
    result = ast.unparse(optimized)

    # Both obj and value should be kept
    assert "obj = get_object()" in result
    assert "value = obj.field" in result
    assert "return value" in result


def test_no_dead_code():
    """Test that optimization works correctly when there's no dead code."""
    code = """
def test_func():
    x = 1
    y = 2
    return x + y
"""
    module = ast.parse(code)
    optimized = eliminate_dead_code(module)
    result = ast.unparse(optimized)

    # All variables used - nothing should be removed
    assert "x = 1" in result
    assert "y = 2" in result
    assert "return x + y" in result


def test_empty_function_body():
    """Test that empty function body gets a pass statement."""
    code = """
def test_func():
    x = 1  # Dead
"""
    module = ast.parse(code)
    optimized = eliminate_dead_code(module)
    result = ast.unparse(optimized)

    # Should have pass statement when all code is removed
    assert "pass" in result


def test_collector_finds_all_assignments_and_loads():
    """Test the internal collection mechanism."""
    code = """
def test_func():
    a = 1
    b = 2
    c = a + b
    d = 3  # Dead
    return c
"""
    module = ast.parse(code)
    eliminator = DeadCodeEliminator()
    eliminator._collect_usage(module)

    # All assignments should be found
    assert "a" in eliminator.assigned_vars
    assert "b" in eliminator.assigned_vars
    assert "c" in eliminator.assigned_vars
    assert "d" in eliminator.assigned_vars

    # Only used variables should be in loaded_vars
    assert "a" in eliminator.loaded_vars
    assert "b" in eliminator.loaded_vars
    assert "c" in eliminator.loaded_vars
    assert "d" not in eliminator.loaded_vars  # Never loaded


def test_function_arguments_not_considered_dead():
    """Test that function parameters are not removed."""
    code = """
def test_func(param1, param2):
    # param2 is never used, but should not be removed
    # (that would break the function signature)
    return param1
"""
    module = ast.parse(code)
    optimized = eliminate_dead_code(module)
    result = ast.unparse(optimized)

    # Function signature should be unchanged
    assert "def test_func(param1, param2):" in result
    # Parameters are not assignments, so they won't be removed


# === Unused Import Removal Tests ===


def test_remove_unused_import():
    """Test that unused imports are removed."""
    code = """
from pathlib import Path
from typing import Any

def test_func():
    return Any
"""
    module = ast.parse(code)
    optimized = remove_unused_imports(module)
    result = ast.unparse(optimized)

    # Path is unused - should be removed
    assert "from pathlib import Path" not in result
    # Any is used - should be kept
    assert "Any" in result


def test_keep_used_import():
    """Test that used imports are kept."""
    code = """
from pathlib import Path

def test_func():
    return Path(".")
"""
    module = ast.parse(code)
    optimized = remove_unused_imports(module)
    result = ast.unparse(optimized)

    # Path is used - should be kept
    assert "from pathlib import Path" in result


def test_remove_entire_unused_import_line():
    """Test that entire import line is removed when all names unused."""
    code = """
from module import unused1, unused2

def test_func():
    return 42
"""
    module = ast.parse(code)
    optimized = remove_unused_imports(module)
    result = ast.unparse(optimized)

    # Entire import should be removed
    assert "from module import" not in result


def test_keep_always_needed_imports():
    """Test that annotations, Any, NamedTuple are always kept."""
    code = """
from __future__ import annotations
from typing import Any, NamedTuple

class Result(NamedTuple):
    value: Any
"""
    module = ast.parse(code)
    optimized = remove_unused_imports(module)
    result = ast.unparse(optimized)

    # These should always be kept
    assert "annotations" in result
    assert "Any" in result
    assert "NamedTuple" in result
