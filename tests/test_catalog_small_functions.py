"""Lexical Class (1126), Match True/False String (1533), Assert Structural Type
Match (2416) and Preserve Run-Time Class (8018)."""

from __future__ import annotations

import pytest

from tests.helpers import error_cluster_type
from tests.prim_vi import Pin, generate, scalar

STRING = scalar("String")
I16 = scalar("NumInt16")
BOOL = scalar("Boolean")
REF = scalar("Refnum")


def _lexical(char_type):
    return generate(
        1126,
        "Lexical Class",
        [Pin(1, "input", char_type, source="c"), Pin(0, "output", I16, sink="n")],
        {"c": char_type},
        {"n": I16},
    )


def test_lexical_class_of_every_code_by_ni_table() -> None:
    source, fn = _lexical(scalar("NumUInt8"))
    assert "lvkit" not in source
    classes = {c: fn(c).n for c in range(256)}
    assert {c for c in range(128, 256) if classes[c] != 0} == set()
    assert [c for c in range(0, 32) if classes[c] == 1] == [
        c for c in range(0, 32) if c not in range(9, 14)
    ]
    assert [c for c in range(256) if classes[c] == 2] == [9, 10, 11, 12, 13, 32]
    assert [c for c in range(256) if classes[c] == 3] == list(range(48, 58))
    assert [c for c in range(256) if classes[c] == 4] == list(range(65, 91))
    assert [c for c in range(256) if classes[c] == 5] == list(range(97, 123))
    assert classes[127] == 1
    printable_other = [c for c in range(33, 127) if not chr(c).isalnum()]
    assert [c for c in range(256) if classes[c] == 6] == printable_other


def test_lexical_class_of_a_string_is_that_of_its_first_byte() -> None:
    _, fn = _lexical(STRING)
    assert fn("a bc").n == 5
    assert fn("7").n == 3
    assert fn(" x").n == 2
    assert fn("\xe9").n == 0
    assert fn("!").n == 6


def test_lexical_class_of_an_array_is_a_gap() -> None:
    from tests.prim_vi import array_of

    _, fn = _lexical(array_of(STRING))
    with pytest.raises(NotImplementedError):
        fn([])


def _match(wired: tuple[bool, bool] = (True, True)):
    controls = {"text": STRING}
    pins = [Pin(4, "input", STRING, source="text")]
    if wired[0]:
        controls["yes"] = STRING
    if wired[1]:
        controls["no"] = STRING
    pins += [
        Pin(3, "input", STRING, source="yes" if wired[0] else None),
        Pin(2, "input", STRING, source="no" if wired[1] else None),
        Pin(0, "output", BOOL, sink="selection"),
        Pin(1, "output", STRING, sink="rest"),
    ]
    return generate(
        1533,
        "Match True/False String",
        pins,
        controls,
        {"selection": BOOL, "rest": STRING},
    )


def test_match_true_false_string() -> None:
    source, fn = _match()
    assert "lvkit" not in source
    row = fn("ON=3", "ON", "OFF")
    assert (row.selection, row.rest) == (True, "=3")
    row = fn("OFF now", "ON", "OFF")
    assert (row.selection, row.rest) == (False, " now")
    row = fn("maybe", "ON", "OFF")
    assert (row.selection, row.rest) == (False, "maybe")


def test_match_true_false_string_is_case_sensitive_and_true_goes_first() -> None:
    _, fn = _match()
    assert fn("on", "ON", "OFF").selection is False
    row = fn("ab", "a", "ab")
    assert (row.selection, row.rest) == (True, "b")


def test_match_true_false_string_unwired_strings_are_an_unknown_default() -> None:
    _, fn = _match((True, False))
    with pytest.raises(NotImplementedError, match="false string"):
        fn("x", "y")


def test_assert_structural_type_match_writes_no_code() -> None:
    source, fn = generate(
        2416,
        "Assert Structural Type Match",
        [Pin(0, "input", REF, source="a"), Pin(1, "input", REF, source="b")],
        {"a": REF, "b": REF},
        {},
    )
    assert "lvkit" not in source
    assert fn(object(), object()) is not False


class Base:
    pass


class Child(Base):
    pass


class Other:
    pass


def _preserve():
    error = error_cluster_type()
    return generate(
        8018,
        "Preserve Run-Time Class",
        [
            Pin(2, "input", REF, source="target"),
            Pin(3, "input", REF, source="obj"),
            Pin(0, "output", REF, sink="out"),
            Pin(1, "output", error, sink="error"),
            Pin(4, "input", error, source=None),
        ],
        {"target": REF, "obj": REF},
        {"out": REF, "error": error},
    )


def test_preserve_run_time_class_passes_an_object_of_the_class_or_a_child() -> None:
    source, fn = _preserve()
    assert "lvkit" not in source
    child = Child()
    row = fn(Base(), child)
    assert row.out is child and row.error is None
    row = fn(Child(), child)
    assert row.out is child and row.error is None


def test_preserve_run_time_class_reports_an_object_of_another_class() -> None:
    _, fn = _preserve()
    row = fn(Base(), Other())
    assert row.error is not None
    assert row.error.source == "Preserve Run-Time Class"
    row = fn(Child(), Base())
    assert row.error is not None
