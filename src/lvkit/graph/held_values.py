"""Outputs whose value their node's input still holds.

LabVIEW passes values by value, so generated code copies a value before it
changes it when another reader can still see it (``codegen/value_copy.py``).
An output that is part of one of the node's own inputs is such a case: an
Unbundle By Name or In Place Element field is held by its cluster, an Index
Array element by its array. The graph build writes that fact once, on the
output terminal (``Terminal.held_in``), and the wire query reads it.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import TerminalDirection
from ..primitive_resolver.models import PrimitiveTerminal
from .models import AnyGraphNode
from .terminal_layout import lay_out_node


def mark_held_outputs(
    graph_node: AnyGraphNode,
    catalog_terminals: Sequence[PrimitiveTerminal] | None,
) -> None:
    """Set ``held_in`` on each output of ``graph_node`` that is part of an input.

    A field output of an Unbundle By Name or In Place Element node (``nmux_role``
    ``"list"``) is held by the node's ``"agg"`` input. With a catalog entry, an
    output whose terminal has ``part_of`` is held by the node terminal placed at
    that catalog index (a resized node repeats the set).
    """
    terminals = graph_node.terminals
    agg_input = next(
        (
            t
            for t in terminals
            if t.nmux_role == "agg" and t.direction == TerminalDirection.INPUT
        ),
        None,
    )
    if agg_input is not None:
        for terminal in terminals:
            if (
                terminal.direction == TerminalDirection.OUTPUT
                and terminal.nmux_role == "list"
            ):
                terminal.held_in = agg_input.id

    if not catalog_terminals:
        return
    parts = {t.index: t.part_of for t in catalog_terminals if t.part_of is not None}
    if not parts:
        return
    laid_out = lay_out_node(catalog_terminals, terminals) or []
    slot_of = {placed.index: placed.slot for placed in laid_out}
    by_slot: dict[int, str] = {}
    for terminal in terminals:
        if terminal.direction == TerminalDirection.INPUT:
            by_slot.setdefault(slot_of.get(terminal.index, terminal.index), terminal.id)
    for terminal in terminals:
        if terminal.direction != TerminalDirection.OUTPUT:
            continue
        part_of = parts.get(slot_of.get(terminal.index, terminal.index))
        if part_of is not None and part_of in by_slot:
            terminal.held_in = by_slot[part_of]
