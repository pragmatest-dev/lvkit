"""Search and Replace String in Regular Expression mode with a pattern computed at
run time: a pattern Python's ``re`` cannot compile is the node's error out.

NI's Regular Expression Error Codes page (labview-api-ref,
errors/regular-expression-error-codes.html) gives one code per kind of pattern
error; ``re.error`` does not say which kind, so the failure takes the generic
code (``native_failure``, plan decision 2), with the exception's text as message.
"""

from __future__ import annotations

import pytest

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


def _replace(project: Project, replace: str = "X") -> Package:  # noqa: F811
    """result = Search and Replace String(text, pattern -> ``replace``), error out
    wired."""
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
    hex_replace = f"{len(replace):08X}{replace.encode().hex().upper()}"
    vi.wire(vi.constant(STR, hex_replace), node["replace string"])
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


def test_a_pattern_pcre_rejects_is_the_error_out(project: Project) -> None:  # noqa: F811
    """A trailing backslash is no PCRE pattern (NI, -4601); LabVIEW reports it on
    error out, so the translator raises ``re.error``: error 42."""
    error = _replace(project).function("abc", "b\\").error_out
    assert (error.code, error.message) == (42, "a backslash at the end of the pattern")


def test_pcre_python_cannot_express_raises_at_run_time(
    project: Project,  # noqa: F811
) -> None:
    """``\\h`` is PCRE (horizontal space) Python re has no form of: a gap, raised
    where the node runs."""
    package = _replace(project)
    with pytest.raises(NotImplementedError, match="has no Python re form"):
        package.function("a b", "\\h")


def test_a_replace_string_naming_a_missing_submatch_raises_at_run_time(
    project: Project,  # noqa: F811
) -> None:
    """NI does not say what ``$2`` gives for a pattern with one submatch: a gap,
    as it is for a constant pattern."""
    package = _replace(project, "<$2>")
    assert package.function("abc", "(b)(c)").result == "a<c>"
    with pytest.raises(NotImplementedError, match="does not have"):
        package.function("abc", "(b)")


def test_a_literal_backslash_g_is_no_submatch(project: Project) -> None:  # noqa: F811
    """A backslash before ``g<5>`` in the replace string is a literal backslash,
    not a reference to submatch 5: no gap for a pattern with one submatch."""
    package = _replace(project, "\\g<5>")
    assert package.function("abc", "(b)").result == "a\\g<5>c"


def test_a_constant_replace_string_with_no_submatch_gets_no_check(
    project: Project,  # noqa: F811
) -> None:
    package = _replace(project, "X")
    assert "does not have" not in package.source
    assert package.function("abc", "(b)").result == "aXc"


@pytest.mark.parametrize(
    ("text", "pattern"), [("a\0c", "b"), ("abc", "b\0")], ids=["input", "pattern"]
)
def test_a_null_character_in_a_wired_string_is_error_4702(
    project: Project,  # noqa: F811
    text: str,
    pattern: str,
) -> None:
    """NI's Search and Replace String page: a null character in a string wired
    to the function is an error; NI's Regular Expression Error Codes: -4702."""
    package = _replace(project)
    assert "class NullCharacterError(ValueError):" in package.source
    error = package.function(text, pattern).error_out
    assert (error.code, error.source) == (-4702, "Search and Replace String")
    assert error.message.startswith("One of the string parameters contains a null")
    assert package.function("abc", "b").error_out is None
