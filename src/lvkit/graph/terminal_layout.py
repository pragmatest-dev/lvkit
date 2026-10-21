"""Where a catalog entry's terminals sit on a node resized to its array types.

An array function grows with the arrays wired to it: one ``index`` input per
dimension of the array, and whole sets of terminals that repeat (Index Array
adds an element output and its indices for each extra set; Replace Array Subset
adds a new element and its indices for each extra replacement; Insert Into Array
adds one more new element). A catalog entry describes this with two optional
terminal fields:

* ``per_dimension_of``: the terminal occupies one index per dimension of the
  array on the terminal that index names.
* ``group``: terminals sharing a group name form one set. If every terminal in
  the set has ``per_dimension_of`` (all naming the same array), the set repeats
  once per dimension, its terminals interleaved (Array Subset: index, length,
  index, length). Otherwise the set repeats as many times as the node's
  terminals make whole sets, and the set must be the entry's last.

Ranks come from the node's own array types, so a node whose rank is unknown, or
whose terminal count does not make whole sets, has no layout.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..models import LVType, LVTypeKind, Terminal


def array_rank(lv_type: LVType | None) -> int | None:
    """Dimension count of an array type; None when the type is not an array."""
    if lv_type is None or lv_type.kind != LVTypeKind.ARRAY:
        return None
    return lv_type.dimensions or 1


@dataclass(frozen=True)
class LaidOutTerminal:
    """One catalog terminal placed at the index it takes on a node."""

    index: int
    direction: str | None
    name: str | None
    slot: int  # the catalog terminal's own index (its role in the entry)
    set_number: int  # which repetition of its group, from 0
    dimension: int  # which dimension of a per-dimension terminal, from 0
    expandable: bool


def _dimension_name(name: str | None, dimension: int, rank: int) -> str | None:
    """``name`` for one dimension, else ``name 0``, ``name 1``, ... as NI's
    pages write ``name 0..n-1``."""
    return f"{name} {dimension}" if name and rank > 1 else name


def _set_name(name: str | None, set_number: int, sets: int) -> str | None:
    """Tell repeated sets apart: ``name (set 1)``, ``name (set 2)``, ..."""
    return f"{name} (set {set_number + 1})" if name and sets > 1 else name


def lay_out(
    catalog: Sequence,
    rank_of: Callable[[int], int | None],
    node_indices: Sequence[int],
) -> list[LaidOutTerminal] | None:
    """Place the entry's terminals on a node whose terminals have ``node_indices``.

    ``rank_of`` gives the dimension count of the array on a node terminal, by
    index. Returns None when the entry cannot be laid out for this node.
    """
    ordered = sorted((t for t in catalog if t.index is not None), key=lambda t: t.index)
    blocks: list[list] = []
    for terminal in ordered:
        group = getattr(terminal, "group", None)
        if (
            group is not None
            and blocks
            and getattr(blocks[-1][0], "group", None) == group
        ):
            blocks[-1].append(terminal)
        else:
            blocks.append([terminal])

    placed: list[LaidOutTerminal] = []
    shift = 0
    for block in blocks:
        grouped = getattr(block[0], "group", None) is not None
        sources = [getattr(t, "per_dimension_of", None) for t in block]
        if not grouped:
            terminal = block[0]
            if sources[0] is None:
                placed.append(_place(terminal, terminal.index + shift, 0, 0, 0, 1))
                continue
            rank = rank_of(sources[0])
            if rank is None:
                return None
            for dimension in range(rank):
                placed.append(
                    _place(
                        terminal,
                        terminal.index + shift + dimension,
                        0,
                        dimension,
                        rank,
                        1,
                    )
                )
            shift += rank - 1
        elif sources[0] is not None and set(sources) == {sources[0]}:
            rank = rank_of(sources[0])
            if rank is None:
                return None
            base = block[0].index + shift
            for dimension in range(rank):
                for position, terminal in enumerate(block):
                    placed.append(
                        _place(
                            terminal,
                            base + dimension * len(block) + position,
                            0,
                            dimension,
                            rank,
                            1,
                        )
                    )
            shift += (rank - 1) * len(block)
        else:
            ranks = [rank_of(s) if s is not None else 1 for s in sources]
            if None in ranks:
                return None
            widths = [r for r in ranks if r is not None]
            unit_size = sum(widths)
            start = block[0].index + shift
            remaining = sum(1 for i in node_indices if i >= start)
            if remaining == 0 or remaining % unit_size:
                return None
            sets = remaining // unit_size
            for set_number in range(sets):
                offset = start + set_number * unit_size
                for terminal, source, width in zip(block, sources, widths):
                    for dimension in range(width):
                        placed.append(
                            _place(
                                terminal,
                                offset + dimension,
                                set_number,
                                dimension,
                                width if source is not None else 1,
                                sets,
                            )
                        )
                    offset += width
            shift += sets * unit_size - len(block)
    return placed


def _place(
    terminal,
    index: int,
    set_number: int,
    dimension: int,
    rank: int,
    sets: int,
) -> LaidOutTerminal:
    name = _set_name(_dimension_name(terminal.name, dimension, rank), set_number, sets)
    return LaidOutTerminal(
        index=index,
        direction=terminal.direction,
        name=name,
        slot=terminal.index,
        set_number=set_number,
        dimension=dimension,
        expandable=bool(getattr(terminal, "expandable", False)),
    )


def lay_out_node(
    catalog: Sequence, node_terminals: Sequence[Terminal]
) -> list[LaidOutTerminal] | None:
    """``lay_out`` for a graph node, with ranks read from its terminals' types."""
    types = {t.index: t.lv_type for t in node_terminals}
    return lay_out(
        catalog,
        lambda index: array_rank(types.get(index)),
        [t.index for t in node_terminals],
    )
