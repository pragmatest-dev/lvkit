"""Catalog terminal names for a node, shown only when the catalog fits the node.

A catalog entry (primitive, node type, or vi.lib VI) names its terminals by
connector-pane index. For a primitive or node type, those names belong on a
node's terminals only when the node really has the entry's shape: every
terminal the node carries must sit at an index the entry defines, in the
direction the entry gives it. When the node disagrees, a name taken by index
alone would land on a terminal of a different slot, so no catalog name is shown
and each terminal keeps its bare index. A vi.lib entry lists only the terminals
callers were seen using, so it is checked terminal by terminal for direction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .terminal_layout import lay_out


class CatalogTerminal(Protocol):
    """The fields of a catalog terminal that name matching reads."""

    @property
    def name(self) -> str | None: ...

    @property
    def index(self) -> int | None: ...

    @property
    def direction(self) -> str | None: ...


@dataclass(frozen=True)
class NodeSlot:
    """One terminal of a node: its connector-pane index and direction."""

    index: int
    direction: str  # "in" or "out", the one spelling every catalog uses


SlotKey = tuple[int, str]


def _direction_fits(catalog_direction: str | None, node_direction: str) -> bool:
    """A catalog terminal with no recorded direction fits either direction."""
    return catalog_direction is None or catalog_direction == node_direction


def catalog_terminal_names(
    slots: Iterable[NodeSlot],
    catalog: Sequence[CatalogTerminal],
    *,
    lists_every_terminal: bool,
    rank_of: Callable[[int], int | None] | None = None,
) -> dict[SlotKey, str]:
    """Names for the node's terminals, keyed by ``(index, direction)``.

    ``lists_every_terminal`` says whether the entry is expected to define every
    terminal of the node (primitives and node types) or only the ones callers
    were seen using (vi.lib VIs).

    A terminal whose index the entry gives the other direction never gets a
    name. When the entry must list every terminal, any such terminal, or any
    terminal at an index the entry does not define, withholds all names from the
    node: the entry then does not describe this node. A terminal whose index is
    still unknown (negative) says nothing either way. An entry that declares an
    expandable terminal allows extra node terminals beyond the indices it
    lists; those extras get no name.

    ``rank_of`` gives the number of dimensions of the array on a node terminal
    (by index), used to lay out terminals the entry repeats per dimension or in
    sets (see ``terminal_layout``); the laid-out terminals stand in for the
    entry when a layout exists.
    """
    node_slots = list(slots)
    if rank_of is not None:
        laid_out = lay_out(catalog, rank_of, [slot.index for slot in node_slots])
        if laid_out is not None:
            catalog = laid_out
    by_index: dict[int, list[CatalogTerminal]] = {}
    for terminal in catalog:
        if terminal.index is not None:
            by_index.setdefault(terminal.index, []).append(terminal)
    can_grow = any(getattr(terminal, "expandable", False) for terminal in catalog)

    if lists_every_terminal:
        for slot in node_slots:
            if slot.index < 0:
                continue
            candidates = by_index.get(slot.index)
            if candidates is None:
                if can_grow:
                    continue
                return {}
            if not any(
                _direction_fits(c.direction, slot.direction) for c in candidates
            ):
                return {}

    names: dict[SlotKey, str] = {}
    for slot in node_slots:
        for terminal in by_index.get(slot.index, ()):
            if terminal.name and _direction_fits(terminal.direction, slot.direction):
                names[(slot.index, slot.direction)] = terminal.name
                break
    return names
