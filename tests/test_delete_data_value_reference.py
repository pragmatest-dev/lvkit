"""New Data Value Reference -> Delete Data Value Reference: the value goes in,
the reference is deleted, and the same value comes out. Executed generated
code."""

from __future__ import annotations

from tests.native_vi import generate, node, scalar

DBL = scalar("NumFloat64")
REF = scalar("Refnum")
ERR = scalar("ErrorCluster")


def _new_then_delete():
    new = node(
        "new",
        2408,
        [
            (4, "input", ERR),
            (7, "input", DBL),
            (0, "output", ERR),
            (3, "output", REF),
        ],
    )
    delete = node(
        "del",
        2409,
        [
            (4, "input", ERR),
            (7, "input", REF),
            (0, "output", ERR),
            (3, "output", DBL),
        ],
    )
    return generate(
        {"value": DBL, "error_in": ERR},
        {"value_out": DBL, "error_out": ERR},
        [new, delete],
        [
            ("fp:value", "new:7"),
            ("fp:error_in", "new:4"),
            ("new:3", "del:7"),
            ("new:0", "del:4"),
            ("del:3", "fp:value_out"),
            ("del:0", "fp:error_out"),
        ],
    )


def test_the_value_comes_back_out_of_the_deleted_reference() -> None:
    result = _new_then_delete().function(2.5, None)  # type: ignore[operator]
    assert result.value_out == 2.5


def test_the_error_passes_through() -> None:
    sentinel = object()
    result = _new_then_delete().function(1.0, sentinel)  # type: ignore[operator]
    assert result.error_out is sentinel


def test_the_generated_source_imports_nothing_from_lvkit() -> None:
    assert "lvkit" not in _new_then_delete().source
