"""The generated package is named from the input file by the same transform as
every module (``module_part``), and never takes the name of a module Python
already has at the top level, so importing it hides nothing."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from lvkit.codegen.ast_utils import package_root_name
from tests.error_diagrams import (
    I32,
    REQUIRED,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)


@pytest.mark.parametrize(
    ("stem", "package"),
    [
        ("Trim Whitespace", "trim_whitespace"),
        ("Main UI", "main_ui"),
        ("1D Array Tools", "var_1d_array_tools"),  # a digit cannot start an import
        ("Class", "class_"),  # a keyword
        ("Numbers", "numbers_"),  # the standard library's numbers
        ("JSON", "json_"),
        ("numpy", "numpy_"),  # a package generated code imports
        ("Errors", "errors"),  # not a top-level module: kept
    ],
)
def test_the_package_is_named_by_the_module_transform(stem: str, package: str) -> None:
    assert package_root_name(stem) == package


@pytest.mark.parametrize("name", ["Numbers.vi", "1D Copy.vi"])
def test_a_vi_named_like_a_module_or_a_number_generates_a_package_that_imports(
    project: Project,  # noqa: F811
    name: str,
) -> None:
    vi = Vi(name)
    vi.wire(vi.control("x", I32, REQUIRED), vi.indicator("y", I32))
    project.add(vi)
    package = project.generate(name)
    assert package.root.name == package_root_name(name.removesuffix(".vi"))
    assert package.function(7).y == 7
    # With the package's folder on the path, "numbers" is still the standard
    # library's.
    spec = importlib.util.find_spec("numbers")
    assert spec is not None and spec.origin is not None
    assert package.root.parent not in Path(spec.origin).parents
