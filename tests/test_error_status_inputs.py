"""An error cluster wired to an input of a Boolean function passes only its
status (NI's pages of And, Or, Not, Select and the other Boolean functions: "If
x is an error cluster, only the status parameter of the error cluster passes to
the input terminal."). A warning (status False with a code) is False there.

Each test builds a tiny diagram, generates its Python and runs it.
"""

from __future__ import annotations

import pytest

from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Status.vi"

AND, NOT, SELECT = 1061, 1064, 1516


def _error_in(package: Package, kind: str) -> object:
    """No error, an error (status True) or a warning (status False, code 5)."""
    if kind == "none":
        return None
    return package.error(5, status=kind == "error")


def _and_of_error_and_flag(project: Project) -> str:  # noqa: F811
    """result = And(x: error in, y: flag)."""
    vi = Vi(NAME)
    ein = vi.error_in()
    flag = vi.control("flag", BOOL, REQUIRED)
    result = vi.indicator("result", BOOL)
    node = vi.primitive(
        AND,
        [("result", 0, BOOL, True), ("y", 1, BOOL, False), ("x", 2, ERR, False)],
    )
    vi.wire(ein, node["x"])
    vi.wire(flag, node["y"])
    vi.wire(node["result"], result)
    project.add(vi)
    return NAME


def _not_of_error(project: Project) -> str:  # noqa: F811
    """result = Not(x: error in)."""
    vi = Vi(NAME)
    ein = vi.error_in()
    result = vi.indicator("result", BOOL)
    node = vi.primitive(NOT, [("not x", 0, BOOL, True), ("x", 1, ERR, False)])
    vi.wire(ein, node["x"])
    vi.wire(node["not x"], result)
    project.add(vi)
    return NAME


def _select_on_error(project: Project) -> str:  # noqa: F811
    """result = Select(s: error in, t: 100, f: 1)."""
    vi = Vi(NAME)
    ein = vi.error_in()
    result = vi.indicator("result", I32)
    node = vi.primitive(
        SELECT,
        [
            ("result", 0, I32, True),
            ("f", 1, I32, False),
            ("s", 2, ERR, False),
            ("t", 3, I32, False),
        ],
    )
    vi.wire(ein, node["s"])
    vi.wire(vi.int_constant(1), node["f"])
    vi.wire(vi.int_constant(100), node["t"])
    vi.wire(node["result"], result)
    project.add(vi)
    return NAME


def _and_of_two_flags(project: Project) -> str:  # noqa: F811
    """result = And(x: a, y: b), no error cluster anywhere."""
    vi = Vi(NAME)
    a = vi.control("a", BOOL, REQUIRED)
    b = vi.control("b", BOOL, REQUIRED)
    result = vi.indicator("result", BOOL)
    node = vi.primitive(
        AND,
        [("result", 0, BOOL, True), ("y", 1, BOOL, False), ("x", 2, BOOL, False)],
    )
    vi.wire(a, node["x"])
    vi.wire(b, node["y"])
    vi.wire(node["result"], result)
    project.add(vi)
    return NAME


@pytest.mark.parametrize(
    ("error", "flag", "expected"),
    [
        ("none", True, False),
        ("none", False, False),
        ("error", True, True),
        ("error", False, False),
        ("warning", True, False),
    ],
)
def test_and_takes_the_status_of_an_error_cluster(
    project: Project,  # noqa: F811
    error: str,
    flag: bool,
    expected: bool,
) -> None:
    package = project.generate(_and_of_error_and_flag(project))
    assert "error_in is not None and error_in.status" in package.source
    result = package.function(error_in=_error_in(package, error), flag=flag)
    assert result.result is expected


@pytest.mark.parametrize(
    ("error", "expected"), [("none", True), ("error", False), ("warning", True)]
)
def test_not_takes_the_status_of_an_error_cluster(
    project: Project,  # noqa: F811
    error: str,
    expected: bool,
) -> None:
    package = project.generate(_not_of_error(project))
    result = package.function(error_in=_error_in(package, error))
    assert result.result is expected


@pytest.mark.parametrize(
    ("error", "expected"), [("none", 1), ("error", 100), ("warning", 1)]
)
def test_select_takes_the_status_of_an_error_cluster(
    project: Project,  # noqa: F811
    error: str,
    expected: int,
) -> None:
    package = project.generate(_select_on_error(project))
    assert package.function(error_in=_error_in(package, error)).result == expected


@pytest.mark.parametrize(
    ("a", "b"), [(True, True), (True, False), (False, True), (False, False)]
)
def test_and_of_two_booleans_is_unchanged(
    project: Project,  # noqa: F811
    a: bool,
    b: bool,
) -> None:
    package = project.generate(_and_of_two_flags(project))
    assert ".status" not in package.source
    assert package.function(a=a, b=b).result is (a and b)


COMPOUND_AND, COMPOUND_OR = 0xA0000, 0xB0000


def _compound_of_error_and_flag(project: Project, mode: int) -> str:  # noqa: F811
    """result = Compound Arithmetic (``mode``) of error in and flag."""
    vi = Vi(NAME)
    ein = vi.error_in()
    flag = vi.control("flag", BOOL, REQUIRED)
    result = vi.indicator("result", BOOL)
    node = vi.compound_arithmetic(
        mode, ("result", BOOL), [("value 0", ERR), ("value 1", BOOL)]
    )
    vi.wire(ein, node["value 0"])
    vi.wire(flag, node["value 1"])
    vi.wire(node["result"], result)
    project.add(vi)
    return NAME


@pytest.mark.parametrize(
    ("mode", "error", "flag", "expected"),
    [
        (COMPOUND_AND, "none", True, False),
        (COMPOUND_AND, "error", True, True),
        (COMPOUND_AND, "warning", True, False),
        (COMPOUND_OR, "none", False, False),
        (COMPOUND_OR, "error", False, True),
        (COMPOUND_OR, "warning", False, False),
        (COMPOUND_OR, "warning", True, True),
    ],
)
def test_compound_arithmetic_takes_the_status_of_an_error_cluster(
    project: Project,  # noqa: F811
    mode: int,
    error: str,
    flag: bool,
    expected: bool,
) -> None:
    """NI's Compound Arithmetic page: "If value is an error cluster, only the
    status parameter of the error cluster passes to the input terminal." """
    package = project.generate(_compound_of_error_and_flag(project, mode))
    assert "error_in is not None and error_in.status" in package.source
    result = package.function(error_in=_error_in(package, error), flag=flag)
    assert result.result is expected
