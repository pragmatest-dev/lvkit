"""The names a caller uses for the VI it calls.

A VI's function takes its parameters by the keyword names, and returns its
results in the fields, that ``interface_names`` makes from its terminals. A
caller reads the same names from the called VI's terminals through the same
function, so two labels that normalise alike are told apart the same way on both
sides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lvkit.graph.interface_order import is_required
from lvkit.graph.models import VINode
from lvkit.models import PaneSlot, Terminal, TerminalDirection

from .ast_utils import interface_names

if TYPE_CHECKING:
    from .context import CodeGenContext


@dataclass(frozen=True)
class CalleeNames:
    """A called VI's parameter keywords and result fields, by connector-pane
    index; ``required`` are the indices of the inputs a caller must wire."""

    parameters: dict[int, str]
    fields: dict[int, str]
    required: frozenset[int] = frozenset()


def callee_names(ctx: CodeGenContext, node: VINode) -> CalleeNames | None:
    """The names of the VI a call node calls, or None when the callee is not
    loaded (the call then names things by its own terminals). A polymorphic VI
    without a pane of its own takes the names of its first variant that has one,
    as the call's terminals do."""
    graph = ctx.graph
    if graph is None:
        return None
    key = graph.loaded_callee_key(node)
    if key is None:
        return None
    candidates = [key, *graph.get_poly_variants(key)]
    for candidate in candidates:
        inputs, off_inputs, outputs, off_outputs = graph.vi_interface(candidate)
        if not (inputs or off_inputs or outputs or off_outputs):
            continue
        names = interface_names(inputs, off_inputs, outputs)
        return CalleeNames(
            parameters={
                t.index: names.parameters[t.id]
                for t in inputs
                if t.id in names.parameters
            },
            fields={
                t.index: names.fields[t.id] for t in outputs if t.id in names.fields
            },
            required=frozenset(
                t.index
                for t in inputs
                if t.index is not None
                and t.id in names.parameters
                and is_required(t, "input")
            ),
        )
    return None


def pane_names(pane: tuple[PaneSlot, ...]) -> CalleeNames:
    """The names of a VI known only by the connector pane its strictly typed
    refnum records: the pane's terminals in slot order, named as the VI's own
    function names them."""
    slots = sorted(pane, key=lambda slot: slot.index)

    def terminals(is_output: bool) -> list[Terminal]:
        return [
            Terminal(
                id=str(slot.index),
                index=slot.index,
                direction=TerminalDirection.of(is_output),
                name=slot.name,
                lv_type=slot.type,
            )
            for slot in slots
            if slot.is_output == is_output
        ]

    inputs, outputs = terminals(False), terminals(True)
    names = interface_names(inputs, [], outputs)
    return CalleeNames(
        parameters={
            t.index: names.parameters[t.id] for t in inputs if t.id in names.parameters
        },
        fields={t.index: names.fields[t.id] for t in outputs if t.id in names.fields},
    )
