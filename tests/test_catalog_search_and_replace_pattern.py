"""Search and Replace Pattern: the caller diagram is generated from the catalog
entry the recorded path names (the pane is the one the measurement-plugin and
DQMH callers record) and its Python is run.

NI, "Search and Replace Pattern VI" (``vi-lib/utility/error-llb/``): replaces the
first match of ``regular expression`` (Match Pattern's syntax) in ``string`` from
``offset``, every match with ``replace all?``; ``offset past match`` is the index
in ``string`` after the last match, -1 with no match; a pattern that matches the
empty string makes no replacements and returns 0, or the length of ``string`` with
``replace all?``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.vilib_caller import BOOL, I32, STRING, run_catalog_call

RECORDED = ["<vilib>", "Utility", "error.llb", "Search and Replace Pattern.vi"]
PANE = [
    (0, "input", STRING),
    (1, "input", STRING),
    (2, "input", STRING),
    (3, "input", I32),
    (4, "input", BOOL),
    (5, "output", STRING),
    (6, "output", I32),
]


def _replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    string: str,
    pattern: str,
    replacement: str | None = None,
    replace_all: bool | None = None,
    offset: int | None = None,
    constant_pattern: bool = False,
) -> tuple[str, Any]:
    inputs: dict[str, Any] = {"text": (0, STRING)}
    arguments: dict[str, Any] = {"text": string}
    constants = {}
    if constant_pattern:
        constants[1] = (pattern, STRING)
    else:
        inputs["pattern"] = (1, STRING)
        arguments["pattern"] = pattern
    for name, index, lv_type, value in (
        ("replacement", 2, STRING, replacement),
        ("offset", 3, I32, offset),
        ("every", 4, BOOL, replace_all),
    ):
        if value is not None:
            inputs[name] = (index, lv_type)
            arguments[name] = value
    return run_catalog_call(
        tmp_path,
        monkeypatch,
        RECORDED,
        PANE,
        inputs,
        {"result": (5, STRING), "past": (6, I32)},
        arguments,
        constants=constants,
        runtime_not_implemented=True,
    )


@pytest.mark.parametrize("constant_pattern", [True, False])
def test_the_first_match_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constant_pattern: bool
) -> None:
    source, result = _replace(
        tmp_path,
        monkeypatch,
        "a1b22c",
        "[0-9]+",
        "#",
        constant_pattern=constant_pattern,
    )
    assert (result.result, result.past) == ("a#b22c", 2)
    assert "lvkit" not in source


def test_replace_all_replaces_every_match_and_past_is_in_the_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _replace(tmp_path, monkeypatch, "a1b22c", "[0-9]+", "#", True)
    assert (result.result, result.past) == ("a#b#c", 5)


def test_no_match_returns_the_string_and_minus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _replace(tmp_path, monkeypatch, "abc", "[0-9]+", "#", True)
    assert (result.result, result.past) == ("abc", -1)


def test_the_replace_string_defaults_to_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _replace(tmp_path, monkeypatch, "a1b", "[0-9]")
    assert (result.result, result.past) == ("ab", 2)


def test_the_replace_string_is_inserted_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _replace(tmp_path, monkeypatch, "a1b", "[0-9]", "$1\\n")
    assert result.result == "a$1\\nb"


def test_the_search_starts_at_the_offset_and_keeps_the_text_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _replace(tmp_path, monkeypatch, "1a1b1", "1", "#", True, 2)
    assert (result.result, result.past) == ("1a#b#", 5)
    _, result = _replace(tmp_path, monkeypatch, "1a1b1", "1", "#", False, 2)
    assert (result.result, result.past) == ("1a#b1", 3)


def test_a_pattern_that_matches_the_empty_string_replaces_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _replace(tmp_path, monkeypatch, "cdb", "b*", "#", False)
    assert (result.result, result.past) == ("cdb", 0)
    _, result = _replace(tmp_path, monkeypatch, "cdb", "b*", "#", True)
    assert (result.result, result.past) == ("cdb", 3)


def test_replace_all_with_an_empty_match_after_a_match_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(NotImplementedError, match="NI does not say"):
        _replace(tmp_path, monkeypatch, "bbcd", "b*", "#", True)


def test_a_match_pattern_special_character_is_a_match_pattern_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Match Pattern's ``.`` is any character but a newline, and ``(`` is
    # ordinary: not PCRE's group.
    _, result = _replace(tmp_path, monkeypatch, "a(b)c", "(b)", "-", False, None, True)
    assert result.result == "a-c"
    _, result = _replace(tmp_path, monkeypatch, "xay", "x.y", "-", False, None, True)
    assert result.result == "-"
