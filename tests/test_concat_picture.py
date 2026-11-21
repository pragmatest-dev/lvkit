"""Concatenate Strings of Pictures draws the later pictures over the earlier ones:
a Picture is a Pillow image, transparent where nothing is drawn."""

from __future__ import annotations

import pytest

from tests.native_vi import generate, node, scalar

PIL_Image = pytest.importorskip("PIL.Image")

PICTURE = scalar("Picture")
STRING = scalar("String")
RED = (255, 0, 0, 255)
BLUE = (0, 0, 255, 255)
CLEAR = (0, 0, 0, 0)


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


def test_the_second_picture_is_drawn_over_the_first() -> None:
    made = _concat(PICTURE, PICTURE, PICTURE)
    first = PIL_Image.new("RGBA", (4, 4), RED)
    second = PIL_Image.new("RGBA", (6, 2), BLUE)
    result = made.function(a=first, b=second).r  # type: ignore[operator]
    assert result.size == (6, 4)
    assert result.getpixel((0, 0)) == BLUE
    assert result.getpixel((3, 1)) == BLUE
    assert result.getpixel((3, 3)) == RED
    assert result.getpixel((5, 3)) == CLEAR
    assert first.size == (4, 4) and second.size == (6, 2)


def test_an_empty_picture_draws_nothing() -> None:
    made = _concat(PICTURE, PICTURE, PICTURE)
    first = PIL_Image.new("RGBA", (2, 2), RED)
    assert made.function(a=None, b=first).r.getpixel((1, 1)) == RED  # type: ignore[operator]
    assert made.function(a=None, b=None).r is None  # type: ignore[operator]


def test_a_string_beside_a_picture_is_the_empty_picture() -> None:
    made = _concat(PICTURE, STRING, PICTURE)
    first = PIL_Image.new("RGBA", (2, 2), RED)
    result = made.function(a=first, b="y").r  # type: ignore[operator]
    assert result.size == (2, 2)
    assert result.getpixel((0, 0)) == RED
