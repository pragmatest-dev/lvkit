"""Delete From Array with its `index` unwired deletes the LAST `length` elements
(NI): one element when `length` is unwired too. Executed generated code."""

from __future__ import annotations

from tests.native_vi import array, generate, node, scalar

I32 = scalar("NumInt32")
ARRAY = array("NumInt32")


def _delete_last(*, length_wired: bool):
    delete = node(
        "dl",
        None,
        [
            (0, "output", ARRAY if length_wired else I32),
            (1, "input", ARRAY),
            (2, "input", I32),
            (3, "output", ARRAY),
            (4, "input", I32),
        ],
        node_type="aDelete",
    )
    inputs = {"a": ARRAY} | ({"n": I32} if length_wired else {})
    wires = [("fp:a", "dl:1"), ("dl:0", "fp:deleted"), ("dl:3", "fp:rest")]
    if length_wired:
        wires.append(("fp:n", "dl:2"))
    return generate(
        inputs,
        {"deleted": ARRAY if length_wired else I32, "rest": ARRAY},
        [delete],
        wires,
    )


def test_an_unwired_index_deletes_the_last_element() -> None:
    result = _delete_last(length_wired=False).function([4, 5, 6])  # type: ignore[operator]
    assert (result.deleted, result.rest) == (6, [4, 5])


def test_an_unwired_index_of_a_single_element_array_leaves_it_empty() -> None:
    result = _delete_last(length_wired=False).function([9])  # type: ignore[operator]
    assert (result.deleted, result.rest) == (9, [])


def test_an_unwired_index_of_an_empty_array_deletes_nothing() -> None:
    result = _delete_last(length_wired=False).function([])  # type: ignore[operator]
    assert result.rest == []
    assert result.deleted in (0, None)


def test_an_unwired_index_with_a_wired_length_deletes_the_last_length_elements() -> (
    None
):
    generated = _delete_last(length_wired=True)
    result = generated.function([1, 2, 3, 4, 5], 2)  # type: ignore[operator]
    assert (result.deleted, result.rest) == ([4, 5], [1, 2, 3])
    assert generated.function([1, 2, 3], 1).rest == [1, 2]  # type: ignore[operator]


def _delete_at(*, length_wired: bool):
    """Delete From Array with ``index`` wired (and ``length`` too, or not)."""
    delete = node(
        "da",
        None,
        [
            (0, "output", ARRAY if length_wired else I32),
            (1, "input", ARRAY),
            (2, "input", I32),
            (3, "output", ARRAY),
            (4, "input", I32),
        ],
        node_type="aDelete",
    )
    inputs = {"a": ARRAY, "i": I32} | ({"n": I32} if length_wired else {})
    wires = [
        ("fp:a", "da:1"),
        ("fp:i", "da:4"),
        ("da:0", "fp:deleted"),
        ("da:3", "fp:rest"),
    ]
    if length_wired:
        wires.append(("fp:n", "da:2"))
    return generate(
        inputs,
        {"deleted": ARRAY if length_wired else I32, "rest": ARRAY},
        [delete],
        wires,
    )


def test_an_index_in_the_array_deletes_from_there() -> None:
    run = _delete_at(length_wired=True).function  # type: ignore[union-attr]
    result = run([1, 2, 3, 4, 5], 1, 2)
    assert (result.deleted, result.rest) == ([2, 3], [1, 4, 5])
    single = _delete_at(length_wired=False).function([1, 2, 3], 1)  # type: ignore[union-attr]
    assert (single.deleted, single.rest) == (2, [1, 3])


def test_a_negative_index_deletes_length_minus_its_size_from_the_start() -> None:
    # NI: "deleted portion is an array with an outer dimension size of the value
    # of length minus the absolute value of the index."
    result = _delete_at(length_wired=True).function([1, 2, 3, 4, 5], -1, 3)  # type: ignore[union-attr]
    assert (result.deleted, result.rest) == ([1, 2], [3, 4, 5])


def test_a_negative_index_beyond_length_deletes_nothing() -> None:
    # NI: "If the index is a negative value and the value of length is smaller
    # than the absolute value of the index, deleted portion is empty."
    result = _delete_at(length_wired=True).function([1, 2, 3], -3, 2)  # type: ignore[union-attr]
    assert (result.deleted, result.rest) == ([], [1, 2, 3])


def test_a_single_element_out_of_range_deletes_nothing() -> None:
    run = _delete_at(length_wired=False).function  # type: ignore[union-attr]
    for index in (-1, 3, 9):
        result = run([1, 2, 3], index)
        assert (result.deleted, result.rest) == (0, [1, 2, 3])


def test_a_run_past_the_end_deletes_to_the_end() -> None:
    run = _delete_at(length_wired=True).function  # type: ignore[union-attr]
    assert run([1, 2, 3], 2, 5).rest == [1, 2]
    assert run([1, 2, 3], 7, 2).rest == [1, 2, 3]


def test_a_length_longer_than_the_array_deletes_all_of_it() -> None:
    result = _delete_last(length_wired=True).function([1, 2, 3], 5)  # type: ignore[operator]
    assert (result.deleted, result.rest) == ([1, 2, 3], [])
