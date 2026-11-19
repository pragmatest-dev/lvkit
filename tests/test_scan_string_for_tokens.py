"""Scan String For Tokens (primResID 8101), per NI's page
(labview-api-ref ``functions/scan-string-for-tokens.html``): the page's worked
example is run through the generated code, and what the page leaves open is a gap.
"""

from __future__ import annotations

import pytest

from lvkit.codegen.gaps import Gap
from tests.native_vi import array, generate, node, result_values, scalar

STRING = scalar("String")
INT = scalar("NumInt32")
BOOL = scalar("Boolean")
STRINGS = array("String")


def _node():
    return node(
        "st",
        8101,
        [
            (0, "output", INT),
            (1, "output", STRING),
            (2, "output", INT),
            (3, "output", STRING),
            (4, "input", BOOL),
            (5, "input", BOOL),
            (6, "input", STRINGS),
            (7, "input", STRINGS),
            (8, "input", INT),
            (9, "input", STRING),
        ],
    )


_OUTPUTS = {"index": INT, "token": STRING, "past": INT, "same": STRING}
_OUTPUT_WIRES = [
    ("st:0", "fp:index"),
    ("st:1", "fp:token"),
    ("st:2", "fp:past"),
    ("st:3", "fp:same"),
]


def _scan(operators=None, delimiters=False, allow_empty=None, gaps=None):
    inputs = {"s": STRING, "o": INT}
    wires = [("fp:s", "st:9"), ("fp:o", "st:8"), *_OUTPUT_WIRES]
    constants = {}
    if delimiters:
        inputs["d"] = STRINGS
        wires.append(("fp:d", "st:6"))
    if operators is not None:
        wires.append(("ops", "st:7"))
        constants["ops"] = ("", STRINGS, operators)
    if allow_empty is not None:
        wires.append(("empty", "st:5"))
        constants["empty"] = (str(allow_empty), BOOL)
    return generate(inputs, _OUTPUTS, [_node()], wires, constants=constants, gaps=gaps)


def _tokens(run, text: str, *extra) -> list[tuple]:
    offset, out = 0, []
    while True:
        index, token, offset_past, same = result_values(
            run.function(text, offset, *extra)
        )
        assert same == text
        out.append((token, index, offset_past))
        if index == -2:
            return out
        offset = offset_past


def test_the_worked_example_on_the_page() -> None:
    run = _scan(operators=["NI", "asdf", "LabVIEW"])
    assert _tokens(run, "This is a testLabVIEWstring") == [
        ("This", -1, 5),
        ("is", -1, 8),
        ("a", -1, 10),
        ("test", -1, 14),
        ("LabVIEW", 2, 21),
        ("string", -1, -1),
        ("", -2, -1),
    ]


def test_the_default_delimiters_are_white_space_and_repeats_count_once() -> None:
    run = _scan()
    assert _tokens(run, "  a \t b\r\n\nc") == [
        ("a", -1, 6),
        ("b", -1, 10),
        ("c", -1, -1),
        ("", -2, -1),
    ]


def test_wired_delimiters_replace_the_default() -> None:
    run = _scan(delimiters=True)
    assert _tokens(run, "a b,,c", [","]) == [
        ("a b", -1, 5),
        ("c", -1, -1),
        ("", -2, -1),
    ]


def test_the_longest_operator_wins() -> None:
    run = _scan(operators=[">", "=", ">="])
    assert result_values(run.function("4>=0", 0)) == (-1, "4", 1, "4>=0")
    assert result_values(run.function("4>=0", 1)) == (2, ">=", 3, "4>=0")


def test_an_offset_outside_the_string_gives_no_token() -> None:
    run = _scan()
    assert result_values(run.function("abc", -1)) == (-2, "", -1, "abc")
    assert result_values(run.function("abc", 4)) == (-2, "", -1, "abc")


def test_the_module_imports_nothing_from_lvkit() -> None:
    assert "lvkit" not in _scan().source


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"allow_empty": True}, "allow empty tokens?"),
        ({"operators": ["%d"]}, "format code"),
        ({"operators": ["-"]}, "a + or - sign"),
    ],
)
def test_what_the_page_leaves_open_is_a_gap(kwargs: dict, reason: str) -> None:
    gaps: list[Gap] = []
    run = _scan(gaps=gaps, **kwargs)
    assert len(gaps) == 1
    assert reason in gaps[0].message
    with pytest.raises(NotImplementedError, match="Scan String For Tokens"):
        run.function("a b", 0)
