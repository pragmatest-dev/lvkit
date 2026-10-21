"""Match Pattern's own pattern syntax, per NI's public pages:

- Match Pattern Function (labview-api-ref ``functions/match-pattern.html``)
- Special Characters for Match Pattern (labview
  ``special-characters-for-match-pattern.html``)

Every test runs the translated pattern through ``match_pattern``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lvkit.runtime.match_pattern import match_pattern, to_python_regex

_PRIMITIVES = (
    Path(__file__).parent.parent
    / "src"
    / "lvkit"
    / "data"
    / "primitives"
    / "string.json"
)


def _match(string: str, pattern: str, offset: int = 0) -> str:
    return match_pattern(string, pattern, offset)[1]


class TestSpecialCharacters:
    def test_period_matches_any_char_except_newline(self) -> None:
        # NI example: "Welcome to LabVIEW." with t.... -> "to La"
        assert _match("Welcome to LabVIEW.", "t....") == "to La"
        assert _match("a\nb", "a.b") == ""

    def test_period_is_literal_in_brackets(self) -> None:
        # NI example: [z.] matches "." or "z"
        assert _match("Welcome.", "[z.]") == "."
        assert _match("abc", "[z.]") == ""

    def test_star_plus_question(self) -> None:
        assert _match("Hello LabVIEW!", "el*") == "ell"
        assert _match("Hello LabVIEW!", "el+") == "ell"
        assert _match("Hello LabVIEW!", "el?") == "el"
        assert _match("Hello", "z*") == ""

    def test_quantifier_with_nothing_before_it_is_literal(self) -> None:
        assert _match("a*b", "*") == "*"
        assert _match("a+b", "+") == "+"

    def test_stacked_quantifiers_apply_to_the_preceding_unit(self) -> None:
        # Python would read "a*?" as non-greedy; Match Pattern has no such thing.
        assert _match("aaa", "a*?") == "aaa"

    def test_version_number_example(self) -> None:
        assert _match("version=14.0.1", r"[0-9]+\.[0-9]+\.[0-9]+") == "14.0.1"

    def test_parentheses_and_bar_are_ordinary_characters(self) -> None:
        # NI: no grouping or alternation; "(.*)" finds the longest (...) text.
        assert _match("say (hi) now", "(.*)") == "(hi)"
        assert _match("a|b", "a|b") == "a|b"
        assert _match("ab", "a|b") == ""

    def test_longest_parenthesised_without_inner_parens(self) -> None:
        assert _match("x (1 2) (3) y", "([~()]*)") == "(1 2)"

    def test_brace_is_an_ordinary_character(self) -> None:
        assert _match("a{2}", "a{2}") == "a{2}"


class TestCharacterClasses:
    def test_case_sensitive_classes_example(self) -> None:
        assert _match("Volts", "[Vv][Oo][Ll][Tt][Ss]") == "Volts"
        assert _match("vOLTS", "[Vv][Oo][Ll][Tt][Ss]") == "vOLTS"

    def test_space_plus_minus_class(self) -> None:
        assert _match("a+b", "[ +-]") == "+"
        assert _match("a-b", "[ +-]") == "-"
        assert _match("a b", "[ +-]") == " "

    def test_digits_range(self) -> None:
        assert _match("ab123c", "[0-9]+") == "123"

    def test_special_characters_are_literal_inside_brackets(self) -> None:
        assert _match("a*b", "[*]") == "*"
        assert _match("hz+", "hz[+]") == "hz+"
        assert _match("hz", "hz[+]") == ""
        assert _match("a$b", "[$]") == "$"

    def test_whitespace_class_examples(self) -> None:
        assert _match("a  b", r"[\s*]") == " "
        assert _match(" \t\r\nx", r"[\t \r \n \s]+") == " \t\r\n"

    def test_tilde_negation(self) -> None:
        # NI: [~0-9]+ is one or more characters other than digits
        assert _match("12ab3", "[~0-9]+") == "ab"
        # ~ matches non-displayable characters too
        assert _match("12\x01\x023", "[~0-9]+") == "\x01\x02"

    def test_caret_negation_is_displayable_only(self) -> None:
        assert _match("12ab3", "[^0-9]+") == "ab"
        assert _match("12\x01ab3", "[^0-9]+") == "ab"

    def test_caret_negation_includes_space(self) -> None:
        assert _match("12 ab3", "[^0-9]+") == " ab"

    def test_tilde_not_first_in_class_is_literal(self) -> None:
        assert _match("a~b", "[a~]+") == "a~"

    def test_dash_is_a_range_only_between_same_kind_characters(self) -> None:
        assert _match("-", "[a-]") == "-"
        assert _match("c", "[a-g]") == "c"
        assert _match("M", "[L-Q]") == "M"
        assert _match("-", "[+-x]") == "-"
        assert _match("m", "[+-x]") == ""

    def test_escaped_bracket_inside_class(self) -> None:
        assert _match("a]b", r"[\]]") == "]"

    def test_literal_brackets_by_escape(self) -> None:
        # NI: \[ is a left bracket, \] a right bracket
        assert _match("a[b]", r"\[") == "["
        assert _match("a[b]", r"\]") == "]"

    def test_negated_empty_class_matches_any_character(self) -> None:
        assert _match("xy", "[~]") == "x"

    def test_unclosed_bracket_is_an_ordinary_character(self) -> None:
        assert _match("a[b", "[b") == "[b"


class TestAnchors:
    def test_caret_anchors_at_the_offset(self) -> None:
        assert match_pattern("Level 1 Level", "^Level", 0)[1] == "Level"
        assert match_pattern("a Level", "^Level", 0)[1] == ""
        assert match_pattern("a Level", "^Level", 2) == ("a ", "Level", "", 7)

    def test_caret_not_first_is_ordinary(self) -> None:
        assert _match("a^b", "a^b") == "a^b"

    def test_dollar_anchors_at_the_end(self) -> None:
        assert _match("Volts and Volts", "Volts$") == "Volts"
        assert match_pattern("Volts and Volts", "Volts$")[0] == "Volts and "
        assert _match("Volts and more", "Volts$") == ""

    def test_dollar_not_last_is_ordinary(self) -> None:
        assert _match("a$b", "a$b") == "a$b"

    def test_dollar_does_not_match_before_a_trailing_newline(self) -> None:
        assert _match("Volts\n", "Volts$") == ""

    def test_first_word_and_last_word_examples(self) -> None:
        assert _match("Hello LabVIEW!", "^[^ ]+") == "Hello"
        assert _match("Hello LabVIEW!", "[^ ]+$") == "LabVIEW!"

    def test_escaped_anchors_are_literal(self) -> None:
        assert _match("a$", r"a\$") == "a$"
        assert _match("^a", r"\^a") == "^a"


class TestBackslash:
    def test_lone_trailing_backslash_is_a_backslash(self) -> None:
        assert to_python_regex("\\") == r"\\"
        assert _match("C:\\dir", "\\") == "\\"

    def test_double_backslash_is_a_backslash(self) -> None:
        assert _match("C:\\dir", "\\\\") == "\\"

    def test_backslash_cancels_special_characters(self) -> None:
        assert _match("what?", r"\?") == "?"
        assert _match("a.b", r"\.") == "."
        assert _match("a*b", r"\*") == "*"

    def test_named_escapes(self) -> None:
        assert _match("a\bb", r"\b") == "\b"
        assert _match("a\fb", r"\f") == "\f"
        assert _match("a\nb", r"\n") == "\n"
        assert _match("a b", r"\s") == " "
        assert _match("a\rb", r"\r") == "\r"
        assert _match("a\tb", r"\t") == "\t"

    def test_hex_escape(self) -> None:
        # NI: \21 is the exclamation point
        assert _match("Hello!", r"\21") == "!"
        assert _match("a\xabb", r"\AB") == "\xab"

    def test_ni_escape_example(self) -> None:
        string = "Welcome\n to   the LabVIEW Help!"
        pattern = r"come\n\sto\s\s\sthe\sLabVIEW\sHelp\21"
        assert _match(string, pattern) == "come\n to   the LabVIEW Help!"

    def test_lowercase_hex_digits_are_not_a_hex_escape(self) -> None:
        # NI: hex uses 0-9 and UPPER CASE A-F
        assert _match("ab", r"\ab") == "ab"

    def test_backslash_before_other_character_stands_for_it(self) -> None:
        assert _match("q", r"\q") == "q"

    def test_windows_path_separator_as_pattern(self) -> None:
        # A wired separator on Windows is a lone backslash.
        assert match_pattern("a\\b\\c", "\\") == ("a", "\\", "b\\c", 2)


class TestFunction:
    def test_no_match_outputs(self) -> None:
        assert match_pattern("hello", "z") == ("hello", "", "", -1)

    def test_no_match_returns_whole_string_even_with_offset(self) -> None:
        assert match_pattern("hello", "z", 2) == ("hello", "", "", -1)

    def test_split_and_offset_past_match(self) -> None:
        assert match_pattern("abcde", "cd") == ("ab", "cd", "e", 4)

    def test_offset_starts_the_search(self) -> None:
        assert match_pattern("abab", "ab", 1) == ("ab", "ab", "", 4)

    def test_negative_offset_is_zero(self) -> None:
        assert match_pattern("abc", "b", -5) == ("a", "b", "c", 2)

    def test_empty_match_offset_past_match_example(self) -> None:
        # NI: pattern b*, string cdb -> offset past match 0; bbbcd -> 3
        assert match_pattern("cdb", "b*")[3] == 0
        assert match_pattern("bbbcd", "b*")[3] == 3


class TestPrimitiveTemplate:
    def test_template_runs_a_wired_windows_separator(self) -> None:
        """Match Pattern's template calls the runtime translator, so a wired
        pattern of one backslash works instead of reaching ``re`` raw."""
        entry = json.loads(_PRIMITIVES.read_text(encoding="utf-8"))["primitives"][
            "1535"
        ]
        hint = entry["python_code"]
        scope: dict = {"in_0": "C:\\dir\\file.vi", "in_1": "\\", "in_2": 0}
        exec(hint["_import"], scope)
        exec(hint["_body"], scope)
        assert eval(hint["before_substring"], scope) == "C:"
        assert eval(hint["match_substring"], scope) == "\\"
        assert eval(hint["offset_past_match"], scope) == 3

    def test_translator_output_always_compiles(self) -> None:
        for pattern in ["", "[", "[~]", "[^]", "\\", "a**", "^$", "[a-z]+\\21"]:
            re.compile(to_python_regex(pattern))


@pytest.mark.parametrize("separator", ["\\", "/"])
def test_both_platform_separators_work_as_patterns(separator: str) -> None:
    assert _match(f"a{separator}b", separator) == separator
