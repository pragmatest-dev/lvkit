"""Choosing and applying a primitive's terminal numbering for one node.

LabVIEW versions can number the same primitive terminal differently. A catalog
entry lists its terminals once (the primary numbering) and may list further
numberings as ``alternative_terminal_sets``, each mapping a node's
(index, direction) onto a primary terminal. The numbering of a node is the one
numbering that holds every terminal index the node carries; none, or more than
one, is a gap. A chosen numbering is applied by renumbering the entry's
terminals and the ``in_N`` placeholders of its code to the node's numbers, so
every reader of the resolved primitive sees the node's own numbering.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from lvkit.models import TerminalDirection

from .models import PrimitiveTerminal, ResolvedPrimitive, TerminalAlias

Slot = tuple[int, TerminalDirection]  # (connector-pane index, direction)

_INPUT_PLACEHOLDER = re.compile(r"\bin_(\d+)\b")


def _node_to_primary(
    terminals: list[PrimitiveTerminal], alternatives: list[list[TerminalAlias]]
) -> list[dict[Slot, int]]:
    """Each numbering as {node slot: primary index}, the primary one first."""
    primary = {(t.index, t.direction): t.index for t in terminals}
    return [primary] + [
        {(a.index, a.direction): a.terminal for a in alias_set}
        for alias_set in alternatives
    ]


def _describe(slots: Collection[Slot]) -> str:
    return ", ".join(f"{index} {direction}" for index, direction in sorted(slots))


def apply_terminal_set(
    resolved: ResolvedPrimitive,
    alternatives: list[list[TerminalAlias]],
    node_slots: Collection[Slot],
) -> ResolvedPrimitive:
    """The primitive in the node's own numbering, or with ``terminal_set_gap``
    set (and no terminals) when the node fits no single numbering."""
    known = {slot for slot in node_slots if slot[0] >= 0}
    numberings = _node_to_primary(resolved.terminals, alternatives)
    fitting = [m for m in numberings if known <= m.keys()]
    if len(fitting) != 1:
        reason = "match no known numbering" if not fitting else "fit several numberings"
        resolved.terminal_set_gap = (
            f"The terminals of this {resolved.name} node ({_describe(known)}) {reason}"
        )
        resolved.terminals = []
        return resolved
    mapping = fitting[0]
    if mapping is numberings[0]:
        return resolved
    node_index = {(primary, slot[1]): slot[0] for slot, primary in mapping.items()}
    resolved.terminals = [
        t.model_copy(update={"index": node_index[(t.index, t.direction)]})
        for t in resolved.terminals
        if (t.index, t.direction) in node_index
    ]
    in_index = {
        p: n for (p, d), n in node_index.items() if d == TerminalDirection.INPUT
    }

    def renumber(match: re.Match[str]) -> str:
        primary = int(match.group(1))
        return f"in_{in_index[primary]}" if primary in in_index else match.group()

    code = resolved.python_code
    if isinstance(code, str):
        resolved.python_code = _INPUT_PLACEHOLDER.sub(renumber, code)
    elif isinstance(code, dict):
        resolved.python_code = {
            k: _INPUT_PLACEHOLDER.sub(renumber, v) for k, v in code.items()
        }
    if resolved.python_code_int is not None:
        code_int = resolved.python_code_int
        resolved.python_code_int = (
            _INPUT_PLACEHOLDER.sub(renumber, code_int)
            if isinstance(code_int, str)
            else {k: _INPUT_PLACEHOLDER.sub(renumber, v) for k, v in code_int.items()}
        )
    return resolved
