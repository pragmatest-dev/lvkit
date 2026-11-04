"""Search and Replace String in Regular Expression mode with a pattern computed at
run time: a pattern Python's ``re`` cannot compile is the node's error out.

NI's Regular Expression Error Codes page (labview-api-ref,
errors/regular-expression-error-codes.html) gives one code per kind of pattern
error; ``re.error`` does not say which kind, so the failure takes the generic
code (``native_failure``, plan decision 2), with the exception's text as message.
"""

from __future__ import annotations

from lvkit.codegen.nodes.ops.search_and_replace import REGULAR_EXPRESSION_MODE
from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    REQUIRED,
    STR,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Replace.vi"
SEARCH_AND_REPLACE = 3914


def _replace(project: Project) -> Package:  # noqa: F811
    """result = Search and Replace String(text, pattern -> "X"), error out wired."""
    vi = Vi(NAME)
    text = vi.control("text", STR, REQUIRED)
    pattern = vi.control("pattern", STR, REQUIRED)
    result = vi.indicator("result", STR)
    out = vi.error_out()
    node = vi.primitive(
        SEARCH_AND_REPLACE,
        [
            ("input string", 0, STR, False),
            ("replace all?", 1, BOOL, False),
            ("result string", 4, STR, True),
            ("search string", 5, STR, False),
            ("replace string", 7, STR, False),
            ("number of replacements", 8, I32, True),
            ("error in", 11, ERR, False),
            ("error out", 15, ERR, True),
        ],
        obj_flags=REGULAR_EXPRESSION_MODE,
    )
    vi.wire(text, node["input string"])
    vi.wire(pattern, node["search string"])
    vi.wire(vi.constant(STR, "0000000158"), node["replace string"])
    vi.wire(node["result string"], result)
    vi.wire(node["error out"], out)
    project.add(vi)
    return project.generate(NAME)


def test_a_pattern_that_compiles_replaces(project: Project) -> None:  # noqa: F811
    package = _replace(project)
    result = package.function("abc", "b")
    assert (result.result, result.error_out) == ("aXc", None)


def test_a_pattern_re_cannot_compile_is_the_error_out(
    project: Project,  # noqa: F811
) -> None:
    package = _replace(project)
    caught = "    except re_error as exception:\n        result_string = ''\n"
    assert caught in package.source
    result = package.function("abc", "(b")
    error = result.error_out
    assert (error.status, error.code, error.source) == (
        True,
        42,
        "Search and Replace String",
    )
    assert "missing )" in error.message
    assert result.result == ""
