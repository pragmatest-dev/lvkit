"""Which dimensions of an array an Index Array node indexes, and how.

A wired index selects a position in its dimension. The VI saves whether indexing
is disabled for each index; where it does, an unwired index that is not disabled
selects position 0 and a disabled one takes its whole dimension. Without that
saved flag, no wired index means the first selects position 0, and every other
unwired index takes its whole dimension. The output's rank must equal the array's
rank minus the number of selecting indexes; otherwise there is no plan (a gap).
lvnet's shown default and the generated code both read this plan, so they agree.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ..models import LVTypeKind, Terminal


class IndexUse(str, Enum):
    """How one index terminal selects along its dimension."""

    WIRED = "wired"
    ZERO = "zero"
    WHOLE = "whole"


@dataclass(frozen=True)
class IndexPlan:
    """The array, the single output and the index terminals (one per leading
    dimension, in order) with how each is used."""

    array: Terminal
    output: Terminal
    indices: tuple[Terminal, ...]
    uses: tuple[IndexUse, ...]

    @property
    def rank(self) -> int:
        assert self.array.lv_type is not None
        return self.array.lv_type.dimensions or 1

    def use_of(self, terminal: Terminal) -> IndexUse:
        return self.uses[self.indices.index(terminal)]


def _rank(terminal: Terminal) -> int | None:
    """Dimensions of an array-typed terminal; 0 for a value that is not an array;
    None when its type is unread."""
    lv_type = terminal.lv_type
    if lv_type is None:
        return None
    return (lv_type.dimensions or 1) if lv_type.kind == LVTypeKind.ARRAY else 0


def plan_index_array(
    terminals: list[Terminal], wired: Callable[[Terminal], bool]
) -> IndexPlan | None:
    """The plan for an Index Array node with one set of terminals, or None when the
    node does not fit one (several outputs, an unread type, or an output whose type
    does not match the wiring)."""
    array = next((t for t in terminals if t.index == 0), None)
    outputs = [t for t in terminals if t.direction == "output"]
    indices = tuple(
        sorted(
            (t for t in terminals if t.direction == "input" and t.index > 0),
            key=lambda t: t.index,
        )
    )
    if array is None or array.direction != "input" or len(outputs) != 1:
        return None
    array_rank, output_rank = _rank(array), _rank(outputs[0])
    if array_rank is None or output_rank is None or array_rank < 2:
        return None
    if array.lv_type is None or array.lv_type.kind != LVTypeKind.ARRAY:
        return None
    if not 0 < len(indices) <= array_rank:
        return None
    selecting = _selecting(indices, wired)
    if array_rank - output_rank != sum(selecting):
        return None
    uses: list[IndexUse] = []
    for terminal, selects in zip(indices, selecting):
        if wired(terminal):
            uses.append(IndexUse.WIRED)
        else:
            uses.append(IndexUse.ZERO if selects else IndexUse.WHOLE)
    return IndexPlan(array, outputs[0], indices, tuple(uses))


def _selecting(
    indices: tuple[Terminal, ...], wired: Callable[[Terminal], bool]
) -> list[bool]:
    """Which indexes select one position. A wired index selects. When the VI saved
    indexing as disabled on any index, an unwired index selects unless it is
    disabled. Without saved flags, no wired index means the first selects position
    0; every other unwired index takes its whole dimension."""
    if any(t.indexing_disabled for t in indices):
        return [wired(t) or not t.indexing_disabled for t in indices]
    flags = [wired(t) for t in indices]
    if not any(flags):
        flags[0] = True
    return flags
