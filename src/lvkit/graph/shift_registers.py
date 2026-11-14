"""How a loop's shift registers pair up: the left register terminals and the
right ones, in the order the loop lists them, match by position."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

_T = TypeVar("_T")


def pair_by_position(
    lefts: Sequence[_T], rights: Sequence[_T]
) -> list[tuple[_T, _T | None]]:
    """Each left shift register with the right one at the same position
    (``None`` when the loop has fewer right registers than left)."""
    return [
        (left, rights[i] if i < len(rights) else None)
        for i, left in enumerate(lefts)
    ]
