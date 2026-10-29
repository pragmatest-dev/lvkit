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


def test_a_length_longer_than_the_array_deletes_all_of_it() -> None:
    result = _delete_last(length_wired=True).function([1, 2, 3], 5)  # type: ignore[operator]
    assert (result.deleted, result.rest) == ([1, 2, 3], [])
