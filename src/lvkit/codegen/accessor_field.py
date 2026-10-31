"""The private data field a property accessor VI reads or writes, from its diagram.

A getter's value comes out of an Unbundle By Name on the class wire; a setter's
value goes into a Bundle By Name on the class wire; the wires may pass through
structures (the error case structure around it). The field is the one that node's
terminal selects. Any other diagram has no one field: the accessor has logic."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from lvkit.class_property import PropertyRole, value_terminals
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import CaseStructureNode, GraphNode
from lvkit.models import Terminal

from .ast_utils import field_attribute_paths

_STRUCTURE_KIND = "structure"


def _is_structure_terminal(graph: InMemoryVIGraph, terminal_id: str) -> bool:
    node = graph.get_graph_node(graph.terminal_node_id(terminal_id))
    return node is not None and node.kind == _STRUCTURE_KIND


def _ends(graph: InMemoryVIGraph, start: str, forward: bool) -> set[str]:
    """The terminals of nodes that are not structures at the far ends of the wires
    from ``start`` (against the flow unless ``forward``), looking through the
    tunnels of structures."""
    ends: set[str] = set()
    seen = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        neighbours = (
            graph.outgoing_edges(current) if forward else graph.incoming_edges(current)
        )
        for end in neighbours:
            if end.terminal_id in seen:
                continue
            seen.add(end.terminal_id)
            if _is_structure_terminal(graph, end.terminal_id):
                pending.append(end.terminal_id)
            else:
                ends.add(end.terminal_id)
    return ends


def _nmux_terminals(
    graph: InMemoryVIGraph, terminal_ids: set[str], direction: str
) -> list[Terminal]:
    """Those of ``terminal_ids`` that are field terminals of a Bundle/Unbundle By
    Name, of ``direction``."""
    found = []
    for terminal_id in sorted(terminal_ids):
        node = graph.get_graph_node(graph.terminal_node_id(terminal_id))
        term = graph.get_terminal(terminal_id)
        if (
            node is not None
            and node.node_type == "nMux"
            and term is not None
            and term.nmux_role == "list"
            and term.direction == direction
        ):
            found.append(term)
    return found


_CASE_STRUCTURE = "select"
_FREE_LABEL = "label"


def _moves_only_that_field(graph: InMemoryVIGraph, vi_key: str, mux_id: str) -> bool:
    """True when the VI's diagram is the Bundle/Unbundle By Name ``mux_id`` and the
    case structures its wires pass through, with free labels: nothing else runs.
    Each of those case structures is an error case structure (its selector is
    fed by the error cluster) and the node sits in its No Error frame; the Error
    frame holds nothing, so it passes the class wire through."""
    for node in graph.iter_nodes(vi_key):
        if node.id == mux_id or node.kind == _FREE_LABEL:
            continue
        if node.kind == _STRUCTURE_KIND and node.node_type == _CASE_STRUCTURE:
            if not _selected_by_error_cluster(graph, node):
                return False
            continue
        return False
    return _in_no_error_frames(graph, graph.get_graph_node(mux_id))


def _selected_by_error_cluster(graph: InMemoryVIGraph, node: GraphNode) -> bool:
    return (
        isinstance(node, CaseStructureNode)
        and node.selector_terminal is not None
        and graph.terminal_fed_by_error_cluster(node.selector_terminal)
    )


def _in_no_error_frames(graph: InMemoryVIGraph, node: GraphNode | None) -> bool:
    """Whether ``node`` sits, through every structure around it, in a No Error
    frame."""
    while node is not None and node.parent is not None:
        parent = graph.get_graph_node(node.parent)
        if not isinstance(parent, CaseStructureNode):
            return False
        frame = next(
            (f for f in parent.frames if str(f.selector_value) == str(node.frame)),
            None,
        )
        if frame is None or not frame.is_no_error:
            return False
        node = parent
    return True


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
    ends = _ends(graph, terminal_id, False)
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


def _is_wired(graph: InMemoryVIGraph, term: Terminal) -> bool:
    return bool(graph.incoming_edges(term.id) or graph.outgoing_edges(term.id))


def _hands_back_object(
    graph: InMemoryVIGraph,
    node: GraphNode,
    self_in: Terminal,
    outputs: Sequence[Terminal],
    is_class: Callable[[Terminal], bool],
) -> bool:
    """Whether the Bundle By Name ``node``'s changed object is what the VI's class
    output gives (the Error frame of an error case structure gives the input
    object back)."""
    agg_out = [
        t for t in node.terminals if t.nmux_role == "agg" and t.direction == "output"
    ]
    class_out = [t for t in outputs if is_class(t)]
    if len(agg_out) != 1 or len(class_out) != 1:
        return False
    ends = _ends(graph, class_out[0].id, False)
    return agg_out[0].id in ends and ends <= {agg_out[0].id, self_in.id}


def accessed_field(
    inputs: Sequence[Terminal],
    outputs: Sequence[Terminal],
    graph: InMemoryVIGraph,
    class_key: str,
    role: PropertyRole,
    vi_key: str,
) -> int | None:
    """The position among the private data fields the class at ``class_key``
    declares of the one field the accessor VI ``vi_key`` (with connector-pane
    ``inputs`` and ``outputs``) reads (getter) or writes (setter), or None when
    its diagram does not just move that one field between the class wire and the
    value: the Bundle/Unbundle By Name has no other field wired, and a setter's
    Bundle hands the changed object to the class output."""

    def is_class(t: Terminal) -> bool:
        return graph.is_class_wire(t.lv_type, vi_key, class_key)

    self_in = next((t for t in inputs if is_class(t)), None)
    if self_in is None:
        return None
    if role is PropertyRole.GETTER:
        (value,) = value_terminals(outputs, is_class)
        candidates = _nmux_terminals(graph, _ends(graph, value.id, False), "output")
    else:
        (value,) = value_terminals(inputs, is_class)
        candidates = _nmux_terminals(graph, _ends(graph, value.id, True), "input")
    if len(candidates) != 1:
        return None
    term = candidates[0]
    node = graph.get_graph_node(graph.terminal_node_id(term.id))
    assert node is not None
    aggs = [
        t for t in node.terminals if t.nmux_role == "agg" and t.direction == "input"
    ]
    if len(aggs) != 1 or aggs[0].lv_type is None or term.nmux_field_index is None:
        return None
    if _ends(graph, aggs[0].id, False) != {self_in.id}:
        return None
    if any(
        t.id != term.id and t.nmux_role == "list" and _is_wired(graph, t)
        for t in node.terminals
    ):
        return None
    if role is PropertyRole.SETTER and not _hands_back_object(
        graph, node, self_in, outputs, is_class
    ):
        return None
    if not _moves_only_that_field(graph, vi_key, node.id):
        return None
    fields = graph.get_type_fields(aggs[0].lv_type, caller_vi_key=vi_key)
    paths = field_attribute_paths(fields) if fields else []
    if not 0 <= term.nmux_field_index < len(paths):
        return None
    if len(paths[term.nmux_field_index]) != 1:
        return None
    return sum(1 for path in paths[: term.nmux_field_index] if len(path) == 1)
