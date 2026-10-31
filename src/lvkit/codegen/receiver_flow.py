"""Whether a class wire carries the object a VI was called on, changed in place.

A method whose class output is the object it was given, changed in place, does
not return it (``class_model.MethodShape``); an accessor VI is a Python property
only when its class output is that object (``class_attributes``). Both follow the
wire back from the class output through the tunnels of structures."""

from __future__ import annotations

from collections.abc import Callable

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import GraphNode
from lvkit.models import Terminal

_STRUCTURE_KIND = "structure"


def _is_structure_terminal(graph: InMemoryVIGraph, terminal_id: str) -> bool:
    node = graph.get_graph_node(graph.terminal_node_id(terminal_id))
    return node is not None and node.kind == _STRUCTURE_KIND


def _sources(graph: InMemoryVIGraph, start: str) -> set[str]:
    """The terminals of nodes that are not structures at the far ends of the wires
    into ``start``, looking through the tunnels of structures."""
    ends: set[str] = set()
    seen = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for end in graph.incoming_edges(current):
            if end.terminal_id in seen:
                continue
            seen.add(end.terminal_id)
            if _is_structure_terminal(graph, end.terminal_id):
                pending.append(end.terminal_id)
            else:
                ends.add(end.terminal_id)
    return ends


def carries_receiver(
    graph: InMemoryVIGraph,
    terminal_id: str,
    receiver_id: str,
    preserves: Callable[[GraphNode], bool] = lambda node: False,
) -> bool:
    """Whether the wire into ``terminal_id`` is the object that came in at the
    class input ``receiver_id``, changed in place: its sources, through
    structure tunnels, are the input itself, or the class output of a Bundle By
    Name or of a call (``preserves`` says whether the call returns the object it
    was given) that changes an object nothing else still reads, whose class input
    carries the receiver."""
    ends = _sources(graph, terminal_id)
    return bool(ends) and all(
        _end_carries_receiver(graph, end, receiver_id, preserves)
        for end in sorted(ends)
    )


def _callee_receiver_inputs(graph: InMemoryVIGraph, node: GraphNode) -> list[Terminal]:
    """The input terminal of the call ``node`` that is the class wire of the VI it
    calls (the pane slot the callee's own class input has)."""
    callee = getattr(node, "callee_key", None)
    class_key = graph.owning_class_key(callee) if callee else None
    if callee is None or class_key is None:
        return []
    inputs, _, _, _ = graph.vi_interface(callee)
    slots = {
        t.index for t in inputs if graph.is_class_wire(t.lv_type, callee, class_key)
    }
    return [t for t in node.terminals if t.direction == "input" and t.index in slots][
        :2
    ]


def _end_carries_receiver(
    graph: InMemoryVIGraph,
    end: str,
    receiver_id: str,
    preserves: Callable[[GraphNode], bool],
) -> bool:
    if end == receiver_id:
        return True
    node = graph.get_graph_node(graph.terminal_node_id(end))
    term = graph.get_terminal(end)
    if node is None or term is None or term.direction != "output":
        return False
    changed: list[Terminal] = []
    if node.node_type == "nMux" and term.nmux_role == "agg":
        changed = [
            t for t in node.terminals if t.nmux_role == "agg" and t.direction == "input"
        ]
    elif (
        node.node_type in ("iUse", "dynIUse", "callParentDynIUse")
        and term.lv_type is not None
        and term.lv_type.ref_type == "UDClassInst"
        and preserves(node)
    ):
        changed = _callee_receiver_inputs(graph, node)
    return (
        len(changed) == 1
        and not graph.value_is_shared(changed[0].id)
        and carries_receiver(graph, changed[0].id, receiver_id, preserves)
    )
