"""The private data field a property accessor VI reads or writes, from its diagram.

A getter's value comes out of an Unbundle By Name on the class wire; a setter's
value goes into a Bundle By Name on the class wire; the wires may pass through
structures (the error case structure around it). The field is the one that node's
terminal selects. Any other diagram has no one field: the accessor is then written
from its diagram like any method."""

from __future__ import annotations

from lvkit.class_property import PropertyRole, value_terminals
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext
from lvkit.models import Terminal
from lvkit.type_defaults import _is_class_refnum

from ..ast_utils import private_attribute_paths

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


def accessed_attribute(
    vi_context: VIContext,
    graph: InMemoryVIGraph,
    class_name: str,
    role: PropertyRole,
) -> str | None:
    """The attribute (``_<field>``) of the one private data field the accessor VI
    ``vi_context`` reads (getter) or writes (setter), or None when its diagram does
    not just move that one field between the class wire and the value."""
    self_in = next(
        (
            t
            for t in vi_context.inputs
            if t.lv_type is not None and _is_class_refnum(t.lv_type, class_name)
        ),
        None,
    )
    if self_in is None:
        return None
    if role is PropertyRole.GETTER:
        (value,) = value_terminals(vi_context.outputs, class_name)
        candidates = _nmux_terminals(graph, _ends(graph, value.id, False), "output")
    else:
        (value,) = value_terminals(vi_context.inputs, class_name)
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
    fields = graph.get_type_fields(
        aggs[0].lv_type, caller_vi_key=vi_context.qualified_name or vi_context.name
    )
    paths = private_attribute_paths(fields) if fields else []
    if not 0 <= term.nmux_field_index < len(paths):
        return None
    path = paths[term.nmux_field_index]
    return path[0] if len(path) == 1 else None
