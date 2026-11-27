"""Delete From Array of a two-dimensional array deletes along the dimension whose
index is wired (NI: "you can wire only one index input"), and an index of no
dimension means the last row. Executed generated code."""

from __future__ import annotations

import pytest

from tests.native_vi import array, generate, node, scalar

I32 = scalar("NumInt32")
GRID = array("NumInt32", 2)
LINE = array("NumInt32")

_ROWS = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]


def _delete(*, row: bool = False, column: bool = False, length: bool = False):
    """``row`` / ``column`` wire the index of that dimension, ``length`` wires
    the length (the deleted portion then keeps both dimensions)."""
    delete = node(
        "dl",
        None,
        [
            (0, "output", GRID if length else LINE),
            (1, "input", GRID),
            (2, "input", I32),
            (3, "output", GRID),
            (4, "input", I32),
            (5, "input", I32),
        ],
        node_type="aDelete",
    )
    inputs = {"a": GRID}
    wires = [("fp:a", "dl:1"), ("dl:0", "fp:deleted"), ("dl:3", "fp:rest")]
    for wanted, name, terminal in (
        (row, "row", 4),
        (column, "column", 5),
        (length, "n", 2),
    ):
        if wanted:
            inputs[name] = I32
            wires.append((f"fp:{name}", f"dl:{terminal}"))
    return generate(
        inputs,
        {"deleted": GRID if length else LINE, "rest": GRID},
        [delete],
        wires,
    ).function  # type: ignore[union-attr]


def test_a_wired_column_index_deletes_that_column_and_gives_it_as_a_line() -> None:
    result = _delete(column=True)(_ROWS, 0)
    assert result.deleted == [1, 5, 9]
    assert result.rest == [[2, 3, 4], [6, 7, 8], [10, 11, 12]]


def test_a_wired_row_index_deletes_that_row_and_gives_it_as_a_line() -> None:
    result = _delete(row=True)(_ROWS, 1)
    assert result.deleted == [5, 6, 7, 8]
    assert result.rest == [[1, 2, 3, 4], [9, 10, 11, 12]]


def test_no_index_wired_deletes_the_last_row() -> None:
    result = _delete()(_ROWS)
    assert result.deleted == [9, 10, 11, 12]
    assert result.rest == [[1, 2, 3, 4], [5, 6, 7, 8]]


def test_a_wired_length_deletes_that_many_columns_and_keeps_two_dimensions() -> None:
    result = _delete(column=True, length=True)(_ROWS, 1, 2)
    assert result.deleted == [[2, 3], [6, 7], [10, 11]]
    assert result.rest == [[1, 4], [5, 8], [9, 12]]


def test_a_wired_length_with_no_index_deletes_the_last_rows() -> None:
    result = _delete(length=True)(_ROWS, 2)
    assert result.deleted == [[5, 6, 7, 8], [9, 10, 11, 12]]
    assert result.rest == [[1, 2, 3, 4]]


@pytest.mark.parametrize("index", [-1, 4, 9])
def test_a_column_outside_the_array_deletes_nothing(index: int) -> None:
    result = _delete(column=True)(_ROWS, index)
    assert result.deleted == []
    assert result.rest == _ROWS


def test_an_empty_array_deletes_nothing() -> None:
    result = _delete()([])
    assert (result.deleted, result.rest) == ([], [])


def test_two_wired_indexes_are_not_converted() -> None:
    run = _delete(row=True, column=True)
    with pytest.raises(NotImplementedError, match="more than one index"):
        run(_ROWS, 0, 0)
