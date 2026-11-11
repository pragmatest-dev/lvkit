"""Path to Array of Strings (1423): ``relative`` is NI's "If TRUE, it is a relative
path. If FALSE, it is an absolute path", and for <Not A Path> "the array of
strings is empty and relative is FALSE"."""

from __future__ import annotations

import os
from pathlib import Path

from tests.native_vi import Generated, array, generate, node, result_values, scalar

PATH = scalar("Path")
BOOL = scalar("Boolean")
STRINGS = array("String")
# Path to Array of Strings (1423) and Array of Strings to Path (1422) panes.
TO_ARRAY = [(0, "input", PATH), (1, "output", BOOL), (2, "output", STRINGS)]
TO_PATH = [(0, "input", STRINGS), (1, "input", BOOL), (2, "output", PATH)]


def _vi() -> Generated:
    return generate(
        {"p": PATH},
        {"relative": BOOL, "parts": STRINGS},
        [node("pa", 1423, TO_ARRAY)],
        [("fp:p", "pa:0"), ("pa:1", "fp:relative"), ("pa:2", "fp:parts")],
    )


def test_a_relative_path_is_relative() -> None:
    relative, parts = result_values(_vi().function(Path("a/b")))
    assert relative is True
    assert parts == ["a", "b"]


def test_an_absolute_path_is_not_relative_and_starts_at_its_volume() -> None:
    """lvkit writes LabVIEW's absolute path ``C``, ``a`` as ``C:/a`` on Windows and
    ``/C/a`` elsewhere (``path_value.python_path``); the array is ``C``, ``a``."""
    absolute = Path("C:/" if os.name == "nt" else "/C", "a")
    relative, parts = result_values(_vi().function(absolute))
    assert relative is False
    assert parts == ["C", "a"]


def _back() -> Generated:
    """Path to Array of Strings, then Array of Strings to Path (1422) with its
    relative input wired from the first one's relative output."""
    return generate(
        {"p": PATH},
        {"q": PATH},
        [node("pa", 1423, TO_ARRAY), node("ap", 1422, TO_PATH)],
        [("fp:p", "pa:0"), ("pa:2", "ap:0"), ("pa:1", "ap:1"), ("ap:2", "fp:q")],
    )


def test_a_path_round_trips_through_its_array_of_strings() -> None:
    absolute = Path("C:/" if os.name == "nt" else "/C", "a", "b")
    assert result_values(_back().function(absolute)) == (absolute,)
    assert result_values(_back().function(Path("x", "y"))) == (Path("x", "y"),)


def test_not_a_path_is_not_relative_and_has_no_parts() -> None:
    assert result_values(_vi().function(None)) == (False, [])
