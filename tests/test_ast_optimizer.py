"""Tests for AST optimization passes."""

import ast

from lvkit.codegen.ast_optimizer import (
    DeadCodeEliminator,
    eliminate_dead_code,
    remove_duplicate_imports,
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


def test_the_future_import_and_read_typing_names_are_kept():
    """The future import and the typing names a module reads are kept."""
    code = """
from __future__ import annotations
from typing import Any, NamedTuple

class Result(NamedTuple):
    value: Any
"""
    module = ast.parse(code)
    optimized = remove_unused_imports(module)
    result = ast.unparse(optimized)

    assert "from __future__ import annotations" in result
    assert "from typing import Any, NamedTuple" in result


def test_an_unread_any_is_not_imported():
    """A module whose annotations name no `Any` does not import it."""
    code = """
from __future__ import annotations
from typing import Any, NamedTuple

class Result(NamedTuple):
    grpc_id: int
"""
    module = ast.parse(code)
    optimized = remove_unused_imports(module)
    result = ast.unparse(optimized)

    assert "from typing import NamedTuple" in result
    assert "Any" not in result


def _optimized(code: str) -> str:
    return ast.unparse(remove_unused_imports(ast.parse(code)))


def test_a_method_import_it_never_reads_is_removed():
    """An import inside a method body that the method never reads goes."""
    result = _optimized("""
class Service:
    def stop(self):
        from .errors import LabVIEWError
        return 1
""")
    assert "LabVIEWError" not in result
    assert "return 1" in result


def test_a_method_import_it_reads_is_kept():
    """An import inside a method body that the method reads stays there."""
    result = _optimized("""
class Service:
    def stop(self):
        from .errors import LabVIEWError
        from .other import unused
        raise LabVIEWError(1)
""")
    assert "from .errors import LabVIEWError" in result
    assert "unused" not in result


def test_a_method_import_of_a_type_checking_only_typedef():
    """A typedef imported under TYPE_CHECKING for an annotation and again in a
    method that never reads it: the method's import goes, the annotation's
    stays. A method that reads it keeps its own import, since the
    TYPE_CHECKING import binds nothing at run time."""
    result = _optimized("""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .typedefs import Annotation

class Result:
    annotations: list[Annotation]

class Service:
    def details(self):
        from .typedefs import Annotation
        return Result()

    def build(self):
        from .typedefs import Annotation
        return Annotation()
""")
    assert result.count("from .typedefs import Annotation") == 2
    assert "def details(self):\n        return Result()" in result
    assert "def build(self):\n        from .typedefs import Annotation" in result


def test_a_method_import_the_module_already_binds_is_removed():
    """A method import that repeats a top-level run-time import goes."""
    result = _optimized("""
from .errors import LabVIEWError

def check():
    raise LabVIEWError(1)

class Service:
    def stop(self):
        from .errors import LabVIEWError
        raise LabVIEWError(2)
""")
    assert result.count("from .errors import LabVIEWError") == 1
    assert result.startswith("from .errors import LabVIEWError")


def test_a_function_left_with_no_statements_gets_pass():
    """A body that held only unread imports still parses."""
    result = _optimized("""
def f():
    from .errors import LabVIEWError
""")
    assert "def f():\n    pass" in result


def test_an_import_read_only_by_a_nested_function_or_lambda_is_kept():
    result = _optimized("""
def outer():
    from .a import A
    from .b import B
    def inner():
        return A()
    return inner, lambda: B()
""")
    assert "from .a import A" in result
    assert "from .b import B" in result


def test_an_aliased_method_import_beside_an_unaliased_module_import_is_kept():
    result = _optimized("""
import json

def dump(x):
    import json as js
    return js.dumps(x) + json.dumps(x)
""")
    assert "import json as js" in result
    assert result.startswith("import json\n")


def test_a_dotted_import_in_a_function_read_through_its_path_is_kept():
    result = _optimized("""
def run():
    import concurrent.futures
    return concurrent.futures.ThreadPoolExecutor
""")
    assert "import concurrent.futures" in result


def test_a_future_import_is_kept_and_another_annotations_name_is_not():
    result = _optimized("""
from __future__ import annotations
from typing_extensions import annotations as unused

def f():
    return 1
""")
    assert "from __future__ import annotations" in result
    assert "typing_extensions" not in result


def test_reads_outside_the_body_and_local_annotations_do_not_keep_an_import():
    """Decorators, defaults and annotations are not read in the function's own
    body, and a local variable annotation is never evaluated."""
    result = _optimized("""
from __future__ import annotations

def f(x: T = D) -> T:
    from .t import T
    from .d import D
    from .l import L
    y: L = 1
    return y
""")
    assert "from .t" not in result
    assert "from .d" not in result
    assert "from .l" not in result


def test_a_function_import_of_a_name_it_rebinds_is_kept():
    for before, after in (
        ("global E", "pass"),
        ("pass", "E = 2"),
        ("pass", "del E"),
        ("pass", "for E in (): pass"),
    ):
        result = _optimized(f"""
from .errors import E

def g():
    return E

def f():
    {before}
    from .errors import E
    {after}
""")
        compile(result, "<test>", "exec")
        assert result.count("from .errors import E") == 2, (before, after)
    nested = _optimized("""
def outer():
    from .errors import E
    def inner():
        nonlocal E
        E = 3
    return inner
""")
    assert "from .errors import E" in nested


def test_relative_imports_at_different_levels_are_not_duplicates():
    result = ast.unparse(
        remove_duplicate_imports(
            ast.parse("from .x import A\nfrom ..x import A\nfrom .x import A\n")
        )
    )
    assert result == "from .x import A\nfrom ..x import A"
