"""Generated code uses only what Python 3.11 has (the floor ``requires-python``
sets).

The shared modules a package writes and the helper functions written into a
module are parsed as 3.11 source, compiled, scanned for the standard-library
names added after 3.11, and the shared modules are imported. CI runs this file on
every Python of its matrix, 3.11 included.

What it catches: syntax newer than 3.11 (``ast.parse(feature_version=(3, 11))``);
a qualified name (``itertools.batched``, ``from typing import override``) that
the table below lists; and, on the 3.11 job, any name the 3.11 interpreter does
not have when a shared module is imported or its top level runs.

What it cannot: a method called on a value (``Path.walk``, ``queue.shutdown`` on
a variable) or a keyword argument added later (``tempfile``'s ``delete_on_close``)
is invisible to the scan, and a function body the import does not run is checked
only by the scan. The nodes' inline templates (code a node writes into a VI's
own function) are covered only through the tests that run them on the 3.11 job.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import pytest

from lvkit.codegen import labview_format, pcre_regex
from lvkit.codegen.errors_module import ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE
from lvkit.codegen.grpc_server_module import (
    GRPC_SERVER_MODULE_NAME,
    GRPC_SERVER_MODULE_SOURCE,
)
from lvkit.codegen.ini_module import INI_MODULE_NAME, INI_MODULE_SOURCE
from lvkit.codegen.live_io_module import LIVE_IO_MODULE_NAME, LIVE_IO_MODULE_SOURCE
from lvkit.codegen.module_function import function_source
from lvkit.codegen.notifiers_module import (
    NOTIFIERS_MODULE_NAME,
    NOTIFIERS_MODULE_SOURCE,
)
from lvkit.codegen.panels_module import PANELS_MODULE_NAME, PANELS_MODULE_SOURCE
from lvkit.codegen.queue_module import QUEUES_MODULE, PackageQueues
from lvkit.codegen.scan_module import SCAN_MODULE_NAME, SCAN_MODULE_SOURCE
from lvkit.codegen.variants_module import VARIANTS_MODULE_NAME, VARIANTS_MODULE_SOURCE
from lvkit.graph.package_queries import ObtainQueueUse

FLOOR = (3, 11)

# Qualified standard-library names added after 3.11, each with the version the
# Python docs give ("Added in version N", docs.python.org/3/library/<module>.html).
NEWER_THAN_3_11 = {
    "queue.ShutDown": "3.13",
    "itertools.batched": "3.12",
    "typing.override": "3.12",
    "typing.TypeAliasType": "3.12",
    "math.sumprod": "3.12",
    "pathlib.Path.is_junction": "3.12",
    "os.path.isjunction": "3.12",
    "os.path.splitroot": "3.12",
    "calendar.Month": "3.12",
    "random.binomialvariate": "3.12",
    "types.get_original_bases": "3.12",
    "inspect.markcoroutinefunction": "3.12",
    "os.listdrives": "3.12",
    "copy.replace": "3.13",
    "os.process_cpu_count": "3.13",
    "warnings.deprecated": "3.13",
}


def _queues_source() -> str:
    use = ObtainQueueUse(
        node_id="n",
        vi_key="a.vi",
        name=None,
        name_wired=False,
        max_size=None,
        max_size_wired=False,
        created_used=False,
    )
    source = PackageQueues.from_uses([use]).build_module()
    assert source is not None
    return source


def _helper_sources() -> dict[str, str]:
    """The functions written into a generated module, from their own source."""
    helpers = {
        f"labview_format.{_defined(source)}": source
        for source in labview_format.helper_sources(*labview_format._DEPENDENCIES)
    }
    helpers["pcre_regex.pcre_to_python_regex"] = function_source(
        pcre_regex.pcre_to_python_regex, "pcre_to_python_regex"
    )
    return helpers


def _defined(source: str) -> str:
    """The name of the first function or class a source defines."""
    first = ast.parse(source).body[0]
    assert isinstance(first, (ast.FunctionDef, ast.ClassDef))
    return first.name


MODULES = {
    ERRORS_MODULE_NAME: ERRORS_MODULE_SOURCE,
    VARIANTS_MODULE_NAME: VARIANTS_MODULE_SOURCE,
    LIVE_IO_MODULE_NAME: LIVE_IO_MODULE_SOURCE,
    PANELS_MODULE_NAME: PANELS_MODULE_SOURCE,
    SCAN_MODULE_NAME: SCAN_MODULE_SOURCE,
    INI_MODULE_NAME: INI_MODULE_SOURCE,
    NOTIFIERS_MODULE_NAME: NOTIFIERS_MODULE_SOURCE,
    GRPC_SERVER_MODULE_NAME: GRPC_SERVER_MODULE_SOURCE,
    QUEUES_MODULE: _queues_source(),
}
SOURCES = {**MODULES, **_helper_sources()}


def _qualified(node: ast.AST) -> str | None:
    """``a.b.c`` of a name or attribute chain, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _is_shutdown_guard(node: ast.If) -> bool:
    """``if hasattr(_StdQueue, "shutdown"):``: its body runs only where the
    standard library has ``shutdown``, so it may name 3.13's ``queue.ShutDown``."""
    test = node.test
    return (
        isinstance(test, ast.Call)
        and _qualified(test.func) == "hasattr"
        and len(test.args) == 2
        and _qualified(test.args[0]) == "_StdQueue"
        and isinstance(test.args[1], ast.Constant)
        and test.args[1].value == "shutdown"
    )


def newer_names(tree: ast.AST) -> list[str]:
    """The names of ``NEWER_THAN_3_11`` the tree uses, outside a guard that
    runs only where the Python has them."""
    found: list[str] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_shutdown_guard(node):
            for child in node.orelse:
                visit(child)
            return
        if isinstance(node, ast.ImportFrom) and node.module:
            found.extend(
                f"{node.module}.{alias.name}"
                for alias in node.names
                if f"{node.module}.{alias.name}" in NEWER_THAN_3_11
            )
        qualified = _qualified(node)
        if qualified in NEWER_THAN_3_11:
            found.append(qualified)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_generated_source_is_python_3_11_syntax(name: str) -> None:
    source = SOURCES[name]
    tree = ast.parse(source, name, feature_version=FLOOR)
    compile(source, name, "exec")
    assert newer_names(tree) == []


def test_the_scan_sees_a_newer_name_and_skips_the_guarded_one() -> None:
    assert newer_names(ast.parse("import itertools\nitertools.batched(a, 2)")) == [
        "itertools.batched"
    ]
    assert newer_names(ast.parse("from typing import override")) == ["typing.override"]
    guarded = ast.parse(
        "from queue import Queue as _StdQueue\n"
        "if hasattr(_StdQueue, 'shutdown'):\n"
        "    from queue import ShutDown\n"
        "else:\n"
        "    class ShutDown(Exception): ...\n"
    )
    assert newer_names(guarded) == []
    unguarded = ast.parse("import queue\nraise queue.ShutDown")
    assert newer_names(unguarded) == ["queue.ShutDown"]


def test_the_syntax_check_rejects_newer_syntax() -> None:
    with pytest.raises(SyntaxError):
        ast.parse("type X = int", feature_version=FLOOR)


@pytest.fixture
def package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "floor_pkg"
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    for name, source in MODULES.items():
        (root / f"{name}.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    yield root.name
    for key in [k for k in sys.modules if k.split(".")[0] == root.name]:
        del sys.modules[key]


@pytest.mark.parametrize("name", sorted(MODULES))
def test_a_shared_module_imports_on_this_python(package: str, name: str) -> None:
    """On the 3.11 job this runs the module's top level on 3.11."""
    importlib.import_module(f"{package}.{name}")
