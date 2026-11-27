"""Where a generic object reference came from: a front-panel object, a
block-diagram object, or somewhere the diagram does not show.

A Property Node on a generic reference (class ``GObject`` or ``Generic``) reads a
property every front-panel and block-diagram object has, and its class does not
say which kind it is. The reference's origin does: the wire is followed back
(``InMemoryVIGraph.wire_origins``, through structure tunnels and shift registers)
to what made the reference.

- Front panel: a Control Reference constant that references a control, the Panel
  of a VI reference, a row that lists front-panel objects (Controls[], Decorations[],
  All Objects[], Panes[] of a panel, a pane or a cluster, a control's Array
  Element).
- Block diagram: the Diagram of a VI reference, and any reference read from an
  object whose class is a block-diagram class (``is_block_diagram_class``).
- Unknown: a subVI input, a control of this VI, a variant, a method's result; the
  reference is only known when the code runs.

Index Array, To More Specific Class, a node's reference out and Owner pass the
origin of their reference on: the object an Owner reads belongs to the same
front panel or block diagram as the object it is read from. When a wire has several
origins (a case structure fed from several frames) the result is a kind only when
they all agree.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from lvkit.control_properties import is_block_diagram_class
from lvkit.graph.models import AnyGraphNode, PrimitiveNode, WireEnd
from lvkit.graph.netlist_models import ReferenceTargetKind
from lvkit.graph.op_walk import correlate_property_terminals
from lvkit.graph.reference_target import is_reference_constant, reference_target
from lvkit.graph.vi_reference import is_on_vi_reference
from lvkit.models import PropertyDef, Terminal
from lvkit.parser.constants import (
    NODE_CLASS_ARRAY_INDEX,
    NODE_CLASS_INVOKE_NODE,
    NODE_CLASS_PROP_NODE,
)

from . import geometry_rows, panel_reference
from .panel_reference import VI_PANEL_PROPERTY_CODE, PanelRow

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph

# The Diagram property of the VI class (NI: properties-and-methods/vi-server/vi/
# diagram.html, "Returns a reference to the block diagram of the VI"), the code
# its Property Nodes record beside the name Diagram.
VI_DIAGRAM_PROPERTY_CODE = 572

# To More Specific Class (data/primitives/cluster-class-and-variant.json): its
# reference input and its reference output.
TO_MORE_SPECIFIC_CLASS_PRIM_ID = 8016
_CAST_REFERENCE_INPUT = 3

# The reference input of a Property Node, an Invoke Node and Index Array's array.
_REFERENCE_INPUT = 0
# The reference output every Property Node and Invoke Node passes its reference
# through on.
_REFERENCE_OUTPUT = 1

# The rows of the front panel's references that give references to its objects.
_FRONT_PANEL_ROWS = frozenset(
    {
        PanelRow.PANEL,
        PanelRow.CONTROLS,
        PanelRow.DECORATIONS,
        PanelRow.ALL_OBJECTS,
        PanelRow.PANES,
        PanelRow.PANE_CONTROLS,
        PanelRow.CLUSTER_CONTROLS,
        PanelRow.CLUSTER_DECORATIONS,
        PanelRow.CLUSTER_ALL_OBJECTS,
        PanelRow.ARRAY_ELEMENT,
    }
)


class ObjectOrigin(Enum):
    """What a generic reference refers to, by where the diagram made it."""

    FRONT_PANEL = "front panel"
    BLOCK_DIAGRAM = "block diagram"
    UNKNOWN = "unknown"


def reference_origin(graph: InMemoryVIGraph, node: PrimitiveNode) -> ObjectOrigin:
    """What the reference wired into the Property Node ``node`` refers to."""
    return _through(graph, node, _REFERENCE_INPUT, frozenset())


def _origin_of_wire(
    graph: InMemoryVIGraph, terminal_id: str, seen: frozenset[str]
) -> ObjectOrigin:
    if terminal_id in seen:
        return ObjectOrigin.UNKNOWN
    origins = {
        _origin_of_source(graph, node, source, seen | {terminal_id})
        for node, source in graph.wire_origins(terminal_id)
    }
    return origins.pop() if len(origins) == 1 else ObjectOrigin.UNKNOWN


def _input_terminal(node: PrimitiveNode, index: int) -> Terminal | None:
    return next(
        (
            t
            for t in node.terminals
            if t.index == index and t.direction == "input"
        ),
        None,
    )


def _through(
    graph: InMemoryVIGraph, node: PrimitiveNode, index: int, seen: frozenset[str]
) -> ObjectOrigin:
    """The origin of the reference wired to the input ``index`` of ``node``."""
    terminal = _input_terminal(node, index)
    if terminal is None:
        return ObjectOrigin.UNKNOWN
    return _origin_of_wire(graph, terminal.id, seen)


def _origin_of_source(
    graph: InMemoryVIGraph,
    node: AnyGraphNode,
    source: WireEnd,
    seen: frozenset[str],
) -> ObjectOrigin:
    if is_reference_constant(node):
        kind = reference_target(graph, node).kind
        if kind is ReferenceTargetKind.CONTROL:
            return ObjectOrigin.FRONT_PANEL
        return ObjectOrigin.UNKNOWN
    if not isinstance(node, PrimitiveNode):
        return ObjectOrigin.UNKNOWN
    if node.prim_id == TO_MORE_SPECIFIC_CLASS_PRIM_ID:
        return _through(graph, node, _CAST_REFERENCE_INPUT, seen)
    if node.node_type == NODE_CLASS_ARRAY_INDEX:
        return _through(graph, node, _REFERENCE_INPUT, seen)
    if node.node_type == NODE_CLASS_INVOKE_NODE:
        if source.index == _REFERENCE_OUTPUT:
            return _through(graph, node, _REFERENCE_INPUT, seen)
        return ObjectOrigin.UNKNOWN
    if node.node_type != NODE_CLASS_PROP_NODE:
        return ObjectOrigin.UNKNOWN
    prop = _property_of(node, source)
    if prop is None:
        if source.index == _REFERENCE_OUTPUT:
            return _through(graph, node, _REFERENCE_INPUT, seen)
        return ObjectOrigin.UNKNOWN
    return _origin_of_property(graph, node, prop, seen)


def _property_of(node: PrimitiveNode, source: WireEnd) -> PropertyDef | None:
    """The property the value output ``source`` of the Property Node ``node``
    reads; None for its reference out or its error out."""
    for prop, term in correlate_property_terminals(
        node.properties or [], node.terminals, node.property_value_terminal_ids
    ):
        if term is not None and term.id == source.terminal_id:
            return prop
    return None


def _origin_of_property(
    graph: InMemoryVIGraph,
    node: PrimitiveNode,
    prop: PropertyDef,
    seen: frozenset[str],
) -> ObjectOrigin:
    """The origin of the reference the row ``prop`` of ``node`` reads."""
    if is_on_vi_reference(node):
        codes = [segment.code for segment in prop.segments]
        if codes == [VI_PANEL_PROPERTY_CODE]:
            return ObjectOrigin.FRONT_PANEL
        if codes == [VI_DIAGRAM_PROPERTY_CODE]:
            return ObjectOrigin.BLOCK_DIAGRAM
        return ObjectOrigin.UNKNOWN
    if is_block_diagram_class(node.object_name or ""):
        return ObjectOrigin.BLOCK_DIAGRAM
    if panel_reference.panel_row(node, prop) in _FRONT_PANEL_ROWS:
        return ObjectOrigin.FRONT_PANEL
    if [segment.code for segment in prop.segments] == [geometry_rows.OWNER_CODE]:
        return _through(graph, node, _REFERENCE_INPUT, seen)
    return ObjectOrigin.UNKNOWN
