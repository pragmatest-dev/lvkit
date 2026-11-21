"""To Lower Case (prim 1189) is polymorphic: NI's page says it "Converts all
alphabetic characters in string to lowercase characters" and that "the connector
pane displays the default data types for this polymorphic function", so a string
array is lowered element by element. The generated Python is run for a string, an
array of strings and a two-dimensional array."""

from __future__ import annotations

from tests.prim_vi import Pin, array_of, generate, scalar


def _vi(string_type, lowered_type):
    return generate(
        1189,
        "To Lower Case",
        [
            Pin(1, "input", string_type, source="text"),
            Pin(0, "output", lowered_type, sink="lowered"),
        ],
        {"text": string_type},
        {"lowered": lowered_type},
    )


def test_string() -> None:
    string = scalar("String")
    _, fn = _vi(string, string)
    assert fn("AbC").lowered == "abc"


def test_array_of_strings_is_lowered_per_element() -> None:
    strings = array_of(scalar("String"))
    _, fn = _vi(strings, strings)
    assert fn(["AbC", "#Attributes"]).lowered == ["abc", "#attributes"]


def test_two_dimensional_array() -> None:
    grid = array_of(scalar("String"))
    grid.dimensions = 2
    _, fn = _vi(grid, grid)
    assert fn([["A", "b"], ["C"]]).lowered == [["a", "b"], ["c"]]
