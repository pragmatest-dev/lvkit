"""Concatenate Strings of a Picture is a gap: a Picture is an image in Python, with
no string of drawing records to append to."""

from __future__ import annotations

import pytest

from tests.native_vi import generate, node, scalar

PICTURE = scalar("Picture")
STRING = scalar("String")


def _concat(first, second, out):
    return generate(
        inputs={"a": first, "b": second},
        outputs={"r": out},
        nodes=[
            node(
                "n",
                None,
                [(0, "output", out), (1, "input", first), (2, "input", second)],
                node_type="concat",
            )
        ],
        wires=[("fp:a", "n:1"), ("fp:b", "n:2"), ("n:0", "fp:r")],
    )


def test_strings_are_joined() -> None:
    made = _concat(STRING, STRING, STRING)
    assert made.function(a="x", b="y").r == "xy"  # type: ignore[operator]


def test_a_picture_operand_is_a_gap() -> None:
    made = _concat(PICTURE, STRING, PICTURE)
    with pytest.raises(NotImplementedError, match="Picture"):
        made.function(a=None, b="y")  # type: ignore[operator]
