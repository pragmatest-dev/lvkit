"""What an unwired input of a call shows as its default (lvnet §4).

One source per callee. A function in the catalog (a primitive or a vi.lib VI)
records the default on its catalog terminal: a documented value (``default``) or a
documented behaviour (``default_behavior``). A project VI records it on its own
connector pane (the control's saved default, the value its ``uses :`` entry shows).
A value is shown only when it fits the terminal's actual type at the call; one that
does not is reported and the input shows the bare ``default``, as does an input
nothing records a default for.
"""

from __future__ import annotations

import logging

from ..lv_type_codes import is_boolean_type_name, is_string_type_name
from ..models import LVType, LVTypeKind, ScalarValue, Terminal
from ..path_value import is_path_text
from ..primitive_resolver import get_resolver as get_primitive_resolver
from ..terminal_default import DefaultBehavior, TerminalDefault
from ..vilib_resolver import get_resolver as get_vilib_resolver
from .core import InMemoryVIGraph
from .index_array_plan import IndexUse, plan_index_array
from .lvnet_scalar import lvnet_typed_scalar, lvnet_value_token
from .models import AnyGraphNode, PrimitiveNode, VINode
from .netlist_models import DefaultValue
from .terminal_layout import LaidOutTerminal, lay_out_node

BARE = "?"
_logger = logging.getLogger(__name__)

_INTEGER_TYPES = frozenset(
    {
        "NumInt8",
        "NumInt16",
        "NumInt32",
        "NumInt64",
        "NumUInt8",
        "NumUInt16",
        "NumUInt32",
        "NumUInt64",
    }
)
_FLOAT_TYPES = frozenset({"NumFloat32", "NumFloat64", "NumFloatExt"})


def default_fits(value: ScalarValue, lv_type: LVType | None) -> bool:
    """Whether a documented value is a value of the terminal's type."""
    if lv_type is None:
        return False
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return isinstance(value, int) and not isinstance(value, bool)
    if lv_type.kind != LVTypeKind.PRIMITIVE:
        return False
    underlying = lv_type.underlying_type or ""
    if is_boolean_type_name(underlying):
        return isinstance(value, bool)
    if underlying in _INTEGER_TYPES:
        return isinstance(value, int) and not isinstance(value, bool)
    if underlying in _FLOAT_TYPES:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if underlying == "Path":
        return isinstance(value, str) and is_path_text(value)
    if is_string_type_name(underlying):
        return isinstance(value, str)
    return False


def _catalog_terminals(node: AnyGraphNode) -> list[TerminalDefault] | None:
    """The catalog terminals of the function ``node`` calls, or None when the
    catalog has no entry for it."""
    if isinstance(node, PrimitiveNode):
        if node.properties or node.method_name:
            return None
        resolver = get_primitive_resolver()
        if node.prim_id:
            entry = resolver.resolve(prim_id=node.prim_id)
        elif node.node_type:
            entry = resolver.resolve_by_node_type(node.node_type)
        else:
            return None
        return list(entry.terminals) if entry is not None else None
    if isinstance(node, VINode):
        vilib = get_vilib_resolver()
        vi = None
        if node.poly_variant_name:
            vi = vilib.resolve_poly_variant(node.name or "", node.poly_variant_name)
        vi = vi or vilib.resolve_by_name(node.name or "")
        return list(vi.terminals) if vi is not None else None
    return None


def _catalog_terminal(
    catalog: list[TerminalDefault], node: AnyGraphNode, terminal: Terminal
) -> tuple[TerminalDefault | None, LaidOutTerminal | None, list[LaidOutTerminal]]:
    """The catalog terminal ``terminal`` is, or repeats on a node resized to its
    array types, and where the node places it."""
    laid_out = lay_out_node(catalog, node.terminals) or []  # type: ignore[arg-type]
    placed = next((t for t in laid_out if t.index == terminal.index), None)
    slot = placed.slot if placed is not None else terminal.index
    for candidate in catalog:
        if getattr(candidate, "index", None) == slot:
            return candidate, placed, laid_out
    return None, placed, laid_out


def _behavior(
    entry: TerminalDefault,
    placed: LaidOutTerminal | None,
    laid_out: list[LaidOutTerminal],
) -> DefaultBehavior | None:
    """What an unwired copy of ``entry`` holds when that is a documented behaviour:
    for a copy that repeats, the later-dimension one when its set has several
    dimensions (an unwired dimension is taken whole), else the later-set one,
    else the terminal's own."""
    if placed is not None:
        in_set = [
            t
            for t in laid_out
            if t.slot == placed.slot and t.set_number == placed.set_number
        ]
        if len(in_set) > 1 and entry.later_dimension_default_behavior is not None:
            return entry.later_dimension_default_behavior
        if placed.set_number > 0 and entry.later_set_default_behavior is not None:
            return entry.later_set_default_behavior
    return entry.default_behavior


def unwired_behavior(node: AnyGraphNode, terminal: Terminal) -> DefaultBehavior | None:
    """The documented behaviour an unwired input of ``node`` has, or None when the
    catalog documents a value or nothing. Shared by lvnet and the code generator,
    so the two agree."""
    catalog = _catalog_terminals(node)
    if catalog is None:
        return None
    entry, placed, laid_out = _catalog_terminal(catalog, node, terminal)
    return _behavior(entry, placed, laid_out) if entry is not None else None


def pane_default(terminal: Terminal) -> ScalarValue:
    """The default a VI's own connector-pane terminal records, as a typed value (the
    value its ``uses :`` entry shows and a call of it shows on an unwired input)."""
    return lvnet_typed_scalar(terminal.default_value, terminal.lv_type)


def _pane_terminal_at(
    graph: InMemoryVIGraph, node: VINode, index: int | None
) -> Terminal | None:
    """The input the called project VI has at connector-pane slot ``index``."""
    callee = graph.loaded_callee_key(node)
    if callee is None:
        return None
    for candidate in graph.get_inputs(callee):
        if candidate.index == index:
            return candidate
    return None


def instance_default(
    graph: InMemoryVIGraph, node: AnyGraphNode, terminal: Terminal, function: str
) -> DefaultValue:
    """The default an unwired input of ``node`` shows: the documented value or
    ``(<behaviour>)`` from the catalog, or the called project VI's pane default,
    else the bare ``default``."""
    label = terminal.type_descriptor() or BARE
    bare = DefaultValue(literal=BARE, type_descriptor=label)
    planned = _index_array_default(graph, node, terminal, label)
    if planned is not None:
        return planned
    value: ScalarValue = None
    name = terminal.name
    catalog = _catalog_terminals(node)
    if catalog is not None:
        entry, placed, laid_out = _catalog_terminal(catalog, node, terminal)
        if entry is None:
            return bare
        behavior = _behavior(entry, placed, laid_out)
        if behavior is not None:
            return DefaultValue(literal=f"({behavior.value})", type_descriptor=label)
        value, name = entry.default, getattr(entry, "name", name)
    elif isinstance(node, VINode):
        pane = _pane_terminal_at(graph, node, terminal.index)
        if pane is not None:
            value, name = pane_default(pane), pane.name
    if value is None:
        return bare
    if not default_fits(value, terminal.lv_type):
        if catalog is not None or _is_scalar_type(terminal.lv_type):
            _logger.warning(
                "%s: the default %r of input %r is not a value of its type %s; "
                "shown as the bare default",
                function,
                value,
                name,
                label,
            )
        return bare
    return DefaultValue(
        literal=lvnet_value_token(value, terminal.lv_type)
        + _item_suffix(value, terminal.lv_type),
        type_descriptor=label,
    )


def _index_array_default(
    graph: InMemoryVIGraph | None,
    node: AnyGraphNode,
    terminal: Terminal,
    label: str,
) -> DefaultValue | None:
    """The default of an unwired index of an Index Array on a multi-dimensional
    array, from the plan the generated code follows; the bare default when there is
    no plan (the code is a gap); None for any other input."""
    if not isinstance(node, PrimitiveNode) or node.node_type != "aIndx":
        return None
    array = next((t for t in node.terminals if t.index == 0), None)
    if (
        array is None
        or array.lv_type is None
        or array.lv_type.kind != LVTypeKind.ARRAY
        or (array.lv_type.dimensions or 1) < 2
        or terminal.direction != "input"
        or terminal.index == 0
    ):
        return None
    plan = plan_index_array(
        node.terminals,
        lambda t: graph is not None and bool(graph.incoming_edges(t.id)),
    )
    if plan is None or terminal not in plan.indices:
        return DefaultValue(literal=BARE, type_descriptor=label)
    if plan.use_of(terminal) == IndexUse.ZERO:
        return DefaultValue(literal="0", type_descriptor=label)
    literal = f"({DefaultBehavior.ENTIRE_DIMENSION.value})"
    return DefaultValue(literal=literal, type_descriptor=label)


def _is_scalar_type(lv_type: LVType | None) -> bool:
    return lv_type is not None and lv_type.kind in (
        LVTypeKind.PRIMITIVE,
        LVTypeKind.ENUM,
        LVTypeKind.RING,
    )


def _item_suffix(value: ScalarValue, lv_type: LVType | None) -> str:
    from .render_lvnet import lvnet_item_suffix

    return lvnet_item_suffix(lv_type, value)
