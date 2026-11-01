"""The PCRE read of Search and Replace String / Match Regular Expression, per NI's
"Special Characters for Match Regular Expression and Search and Replace String"
(labview bundle). Each construct the page documents is matched through the
translator; the examples are the page's own."""

from __future__ import annotations

import re

import pytest

from lvkit.codegen.module_function import function_source
from lvkit.codegen.pcre_regex import pcre_to_python_regex, pcre_to_python_template


def match(string: str, pattern: str, flags: int = 0) -> str | None:
    found = re.search(pcre_to_python_regex(pattern), string, re.ASCII | flags)
    return None if found is None else found.group()


def replaced(string: str, pattern: str, replacement: str, count: int = 0) -> str:
    return re.sub(
        pcre_to_python_regex(pattern),
        pcre_to_python_template(replacement),
        string,
        count=count,
        flags=re.ASCII,
    )


class TestSpecialCharacters:
    def test_period_is_any_character_but_a_newline(self) -> None:
        assert match("Welcome to LabVIEW.", "t....") == "to La"
        assert match("a\nb", "a.b") is None

    def test_period_is_literal_in_brackets(self) -> None:
        assert match("Welcome.", "[z.]") == "."

    def test_quantifiers_are_greedy_and_question_mark_makes_them_lazy(self) -> None:
        assert match("Hello LabVIEW!", "el*") == "ell"
        assert match("Hello LabVIEW!", "el+") == "ell"
        assert match("Hello LabVIEW!", "el?") == "el"
        html = "<ul><li>Hello</li><li>LabVIEW</li></ul>"
        assert match(html, "<li>.+?</li>") == "<li>Hello</li>"
        assert match(html, "<li>.+</li>") == "<li>Hello</li><li>LabVIEW</li>"

    def test_counted_quantifier(self) -> None:
        html = "<ul><li>Hello</li><li>Lab</li><li>VIEW</li><li>!</li></ul>"
        assert match(html, "(<li>.+?</li>){2}") == "<li>Hello</li><li>Lab</li>"
        assert (
            match(html, "(<li>.+?</li>){1,3}")
            == "<li>Hello</li><li>Lab</li><li>VIEW</li>"
        )

    def test_brace_that_is_not_a_quantifier_is_literal(self) -> None:
        # PCRE reads {,3} and a lone { as text; Python reads {,3} as a quantifier.
        assert match("a{,3}", "a{,3}") == "a{,3}"
        assert match("x{", "x{") == "x{"

    def test_character_class(self) -> None:
        assert match("version=14.0.1", r"[0-9]+(\.[0-9]+)*") == "14.0.1"
        assert match("a*b", "[*]") == "*"

    def test_class_specials_are_literal_except_caret_dash_and_backslash(self) -> None:
        assert match("a[b", "[[]") == "["
        assert match("a&&b", "[&&]+") == "&&"
        assert match("12ab", "[^0-9]+") == "ab"
        assert match("a-b", "[a\\-]+") == "a-"
        assert match("a-b", "[b-]+") == "-b"

    def test_group_and_submatches(self) -> None:
        found = re.search(pcre_to_python_regex("(el.)..(L..)"), "Hello LabVIEW!")
        assert found is not None
        assert (found.group(), found.group(1), found.group(2)) == (
            "ello Lab",
            "ell",
            "Lab",
        )

    def test_alternation_takes_the_first_match_in_the_string(self) -> None:
        assert match("the dog chased the cat", "dog|cat") == "dog"
        assert match("the dog chased the cat", "cat|dog") == "dog"
        string = "value=FALSE total=12.34 token=TRUE"
        assert match(string, "(value|token)=(TRUE|FALSE)") == "value=FALSE"

    def test_anchors_and_multiline(self) -> None:
        assert match("Hello LabVIEW!", "^[^ ]+") == "Hello"
        assert match("Hello LabVIEW!", "[^ ]+$") == "LabVIEW!"
        assert match("Hello\nLabVIEW", "^LabVIEW") is None
        assert match("Hello\nLabVIEW", "^LabVIEW", re.MULTILINE) == "LabVIEW"
        assert match("Hello\nLabVIEW", "Hello$") is None
        assert match("Hello\nLabVIEW", "Hello$", re.MULTILINE) == "Hello"
        assert match("LabVIEW!", "^LabVIEW$") is None
        assert match("LabVIEW", "^LabVIEW$") == "LabVIEW"


class TestBackslash:
    def test_escaped_special_character_is_literal(self) -> None:
        assert match("total=$12.34", r"\$\d+(\.\d{2})?") == "$12.34"

    def test_word_boundary(self) -> None:
        assert match("hatchet", r"\bhat") == "hat"
        assert match("that", r"\bhat") is None
        assert match("that", r"hat\b") == "hat"
        assert match("hatchet", r"hat\b") is None

    def test_word_digit_and_space_classes_are_ascii(self) -> None:
        assert match("a_1 b", r"\w+") == "a_1"
        assert match("é", r"\w") is None
        assert match("ab 12", r"\W") == " "
        assert match("ab12", r"\d+") == "12"
        assert match("12ab", r"\D+") == "ab"
        assert match("ab \t", r"\s+") == " \t"
        assert match("  ab", r"\S+") == "ab"

    def test_control_character(self) -> None:
        assert match("ab\x01c", r"\c") == "\x01"
        assert match("ab c", r"\c") is None

    def test_character_escapes(self) -> None:
        assert match("a\nb", r"\n") == "\n"
        assert match("a\tb", r"\t") == "\t"
        assert match("a\rb", r"\r") == "\r"
        assert match("a\fb", r"\f") == "\f"
        assert match("a\x1bb", r"\e") == "\x1b"

    def test_octal_and_hexadecimal(self) -> None:
        assert match("a\x19b", r"\031") == "\x19"
        assert match("Hello!", r"\x21") == "!"
        assert match("Hello!", r"\x{21}") == "!"
        assert match("a\x0bb", r"\xb") == "\x0b"

    def test_ni_example_with_newline_tab_and_hex(self) -> None:
        string = "Welcome\nto\tLabVIEW!"
        assert match(string, r"come\n\S*\t\w*\x21") == "come\nto\tLabVIEW!"

    def test_backreference(self) -> None:
        assert match('"foo"foo"', r'(["*$])(\w+)\1\2\1') == '"foo"foo"'
        assert match("foo$foo", r'(["*$])(\w+)\1\2\1') is None

    def test_backreference_is_not_read_with_the_digit_after_it(self) -> None:
        assert match("aa0", r"(a)\1" + "0") == "aa0"

    def test_end_of_string_anchors(self) -> None:
        assert match("ab\n", r"b\z") is None
        assert match("ab\n", r"b\Z") == "b"

    def test_an_escape_python_cannot_express_raises_naming_it(self) -> None:
        with pytest.raises(ValueError, match=r"the escape \\h"):
            pcre_to_python_regex(r"a\h")

    def test_unclosed_class_and_trailing_backslash_raise(self) -> None:
        with pytest.raises(ValueError, match="no closing"):
            pcre_to_python_regex("[ab")
        with pytest.raises(ValueError, match="backslash at the end"):
            pcre_to_python_regex("ab\\")


class TestNamedGroup:
    def test_named_group_and_lookbehind(self) -> None:
        found = re.search(pcre_to_python_regex(r"(?<n>\d+)"), "ab12")
        assert found is not None and found.group("n") == "12"
        assert match("ab12", r"(?<=ab)\d") == "1"


class TestReplacement:
    def test_submatch_references(self) -> None:
        assert (
            replaced("Welcome LabVIEW", "(LabVIEW)", "to $1!") == "Welcome to LabVIEW!"
        )

    def test_braces_read_more_than_one_digit(self) -> None:
        string = "Welcome to the LabVIEW Help!"
        pattern = "We(l)(co(m)e)( )(to)( )t(he) (Lab)(VIE(W)) He(lp)(!)"
        assert replaced(string, pattern, "$7${11}ful${12}") == "helpful!"

    def test_dollar_digits_read_one_digit(self) -> None:
        assert replaced("ab", "(a)", "$12") == "a2b"

    def test_escaped_dollar_and_backslash(self) -> None:
        assert (
            replaced("total=123 per day", r"(\d+)(per \w*)?", r"\$$1$2")
            == "total=$123 per day"
        )
        assert replaced("a", "a", r"\\") == "\\"
        assert replaced("a", "a", "\\x") == "\\x"
        assert replaced("a", "a", "$") == "$"
        assert replaced("a", "a", "${x}") == "${x}"

    def test_backreference_example(self) -> None:
        assert (
            replaced('$value "TRUE"TRUE" *NULL', r'(["*$])(\w+)\1\2\1', "$1$2value$1")
            == '$value "TRUEvalue" *NULL'
        )


def test_translators_are_written_into_a_module_from_the_same_source() -> None:
    namespace: dict = {"re": re}
    exec(function_source(pcre_to_python_regex, "translate"), namespace)
    exec(function_source(pcre_to_python_template, "template"), namespace)
    for pattern in [r"a\x21[b-d]+{,2}", r"(?<n>a)|\cb", "[[]"]:
        assert namespace["translate"](pattern) == pcre_to_python_regex(pattern)
    assert namespace["template"]("$1${2}\\$") == pcre_to_python_template("$1${2}\\$")
