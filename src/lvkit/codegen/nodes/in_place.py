"""Code generator for In Place Element Structure (decompose/recompose).

An In Place Element Structure edits one piece of data where it is: a READ tile on
its border takes an element out of the data, the body works on the element, and
a WRITE tile on the opposite border puts it back. Same data, no copies -- the
data is a cluster or class (Unbundle/Bundle Elements), an array (Array
Index/Replace Elements) or a data value reference (DVR Read/Write).

A tile's terminals and the structure's own input and output ports that carry the
same data share a data-flow id (``dco_uid``): that link, not the order of the
ports, says which value enters which tile.

generate() is structured as three explicit passes:
  Input boundary  — bind input tunnels, bind tile inputs to the structure's
                    ports, then take each element out of its data
  Body            — generate_body(regular nodes)
  Output boundary — write the elements back + bind output tunnels + bind the
                    structure's data output ports
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.graph.models import AnyGraphNode, InPlaceNode, PrimitiveNode
from lvkit.models import (
    ClusterField,
    InPlaceBorderSide,
    Terminal,
    Tunnel,
    inplace_border_side,
)

from ..ast_utils import parse_expr
from ..class_attributes import ClassAttributes
from ..class_model import type_attributes
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..value_copy import owned_input
from .nmux import _attribute_target, _field_expr, _field_path

_CLUSTER = "decomposeClusterNode"
_ARRAY = "decomposeArrayNode"
_DVR = "decomposeDataValRefNode"
_BORDER_NODE_TYPES = (_CLUSTER, _ARRAY, _DVR)


@dataclass
class _Tile:
    """One tile of the structure's border."""

    op: PrimitiveNode
    side: InPlaceBorderSide
    kind: str
    # The tile's other half is the one of the same kind with the same pairing id
    # (the heap's ``poser``); the data value reference tiles carry none, so their
    # two halves pair as the only two of their kind.
    pair: tuple[str, str | None]


@dataclass
class _Element:
    """The data an In Place Element Structure edits: the variable that holds it
    and, for an array, the variable that indexes the element."""

    whole: str
    index: str | None = None


def _split_children(
    children: list[AnyGraphNode], ctx: CodeGenContext
) -> tuple[list[_Tile], list[AnyGraphNode]]:
    """Split the structure's inner graph nodes into border tiles and regular body
    nodes. A cluster tile is a node with a pairing id and field terminals in one
    direction only; an array or data value reference tile is told by its node
    type."""
    tiles: list[_Tile] = []
    regular: list[AnyGraphNode] = []
    for op in children:
        node_type = None
        side = None
        if isinstance(op, PrimitiveNode):
            if op.node_type in (_ARRAY, _DVR):
                node_type = op.node_type
            elif ctx.poser_uid(op):
                node_type = _CLUSTER
            if node_type is not None:
                side = inplace_border_side(node_type, op.terminals)
        if isinstance(op, PrimitiveNode) and node_type is not None and side is not None:
            tiles.append(_Tile(op, side, node_type, (node_type, ctx.poser_uid(op))))
        else:
            regular.append(op)
    return tiles, regular


def generate(node: InPlaceNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for an In Place Element Structure."""
    all_stmts: list[ast.stmt] = []
    all_bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    tunnels = ctx.tunnels(node)
    tiles, inner_nodes = _split_children(ctx.child_nodes(node), ctx)

    # --- Input boundary ---
    _bind_input_tunnels(node, tunnels, ctx)
    tunnel_uids = {
        uid for t in tunnels for uid in (t.outer_terminal_uid, t.inner_terminal_uid)
    }
    _own_data_inputs(node, tunnel_uids, ctx, all_stmts)
    _bind_tile_inputs(node, tiles, ctx)
    data_var = _find_data_var(node, tunnel_uids, ctx)
    elements = _take_elements_out(tiles, data_var, ctx)

    # --- Body (regular inner ops only) ---
    body_stmts = ctx.generate_body(inner_nodes)
    for stmt in body_stmts:
        ast.fix_missing_locations(stmt)
    all_stmts.extend(body_stmts)
    all_imports.update(ctx.imports)

    # --- Output boundary ---
    all_stmts.extend(_write_elements_back(tiles, elements, ctx))

    # Regular field-value tunnels: inner → outer (output direction only).
    _bind_output_tunnels(node, tunnels, ctx, all_bindings)

    # The data comes back out of the structure on its output ports.
    producers = [*(t.op for t in tiles), *inner_nodes]
    _bind_data_outputs(node, producers, data_var, tunnel_uids, all_bindings, ctx)

    return CodeFragment(
        statements=all_stmts,
        bindings=all_bindings,
        imports=all_imports,
    )


# ---------------------------------------------------------------------------
# Input boundary helpers
# ---------------------------------------------------------------------------


def _bind_input_tunnels(
    node: InPlaceNode, tunnels: list[Tunnel], ctx: CodeGenContext
) -> None:
    """Propagate outer → inner for input-direction tunnels only."""
    outer_id_to_term = {t.id: t for t in node.terminals}
    for tunnel in tunnels:
        outer_term = outer_id_to_term.get(tunnel.outer_terminal_uid)
        if not outer_term or outer_term.direction != "input":
            continue
        outer_var = ctx.resolve(tunnel.outer_terminal_uid)
        if outer_var:
            ctx.bind(tunnel.inner_terminal_uid, outer_var)


def _own_data_inputs(
    node: InPlaceNode,
    tunnel_uids: set[str],
    ctx: CodeGenContext,
    statements: list[ast.stmt],
) -> None:
    """The structure changes its data where it is: an input port that carries a
    class object or a named cluster something else can still read is given a copy
    of it."""
    for port in node.terminals:
        if port.direction != "input" or port.id in tunnel_uids:
            continue
        value = ctx.wired_input(port.id, node)
        owned = owned_input(ctx, port, node, statements)
        if owned is not None and owned != value:
            ctx.bind(port.id, owned)


def _bind_tile_inputs(
    node: InPlaceNode, tiles: list[_Tile], ctx: CodeGenContext
) -> None:
    """Give each tile input the value of the structure input port that carries the
    same data (same ``dco_uid``). A tile input the diagram wires itself keeps
    that wire."""
    ports = [t for t in node.terminals if t.direction == "input"]
    for tile in tiles:
        for term in tile.op.terminals:
            if term.direction != "input" or term.dco_uid is None:
                continue
            if ctx.resolve(term.id) is not None or (
                ctx.graph is not None and ctx.graph.terminal_is_fed(term.id)
            ):
                continue
            for port in ports:
                if port.dco_uid == term.dco_uid:
                    value = ctx.wired_input(port.id, node)
                    if value is not None:
                        ctx.bind(term.id, value)
                    break


def _take_elements_out(
    tiles: list[_Tile], data_var: str | None, ctx: CodeGenContext
) -> dict[tuple[str, str | None], _Element]:
    """Bind what each READ tile hands the body, and what each WRITE tile hands
    back out, to expressions over the edited data. Returns the edited data of
    each tile pair. ``data_var`` is the structure's one data variable, for a
    cluster tile whose input carries no data-flow id."""
    elements: dict[tuple[str, str | None], _Element] = {}
    for tile in tiles:
        if tile.side != InPlaceBorderSide.READ:
            continue
        element = _take_element_out(tile, data_var, ctx)
        if element is not None:
            elements[tile.pair] = element
    for tile in tiles:
        element = _element_of(tile, elements)
        if tile.side == InPlaceBorderSide.WRITE and element is not None:
            for out in _carried_outputs(tile):
                ctx.bind(out.id, element.whole)
    return elements


def _element_of(
    tile: _Tile, elements: dict[tuple[str, str | None], _Element]
) -> _Element | None:
    """The data a tile edits: that of its pair, else the only one of its kind."""
    if tile.pair in elements:
        return elements[tile.pair]
    of_kind = [e for (kind, _pair), e in elements.items() if kind == tile.kind]
    return of_kind[0] if len(of_kind) == 1 else None


def _take_element_out(
    tile: _Tile, data_var: str | None, ctx: CodeGenContext
) -> _Element | None:
    op = tile.op
    if tile.kind == _CLUSTER:
        agg = _role_terminal(op, "agg", "input")
        whole = (ctx.wired_input(agg.id, op) if agg is not None else None) or data_var
        if agg is None or whole is None:
            return None
        class_fields = _get_class_fields(agg, ctx)
        for t in _field_terminals(op, "output"):
            ctx.bind(t.id, _field_expr(t, whole, class_fields, _class_attrs(agg, ctx)))
        return _Element(whole)
    if tile.kind == _ARRAY:
        array_in = _role_terminal(op, None, "input")
        index_in = _role_terminal(op, "list", "input")
        element_out = _role_terminal(op, "agg", "output")
        if array_in is None or index_in is None or element_out is None:
            return None
        whole = ctx.wired_input(array_in.id, op)
        index = ctx.wired_input(index_in.id, op)
        if whole is None or index is None:
            return None
        ctx.bind(element_out.id, f"{whole}[{index}]")
        return _Element(whole, index)
    dvr_in = next(
        (t for t in op.terminals if t.direction == "input"),
        None,
    )
    value_out = next(iter(_data_terminals(op, "output")), None)
    whole = ctx.wired_input(dvr_in.id, op) if dvr_in is not None else None
    if whole is None or value_out is None:
        return None
    ctx.bind(value_out.id, f"{whole}[0]")
    return _Element(whole)


# ---------------------------------------------------------------------------
# Output boundary helpers
# ---------------------------------------------------------------------------


def _write_elements_back(
    tiles: list[_Tile],
    elements: dict[tuple[str, str | None], _Element],
    ctx: CodeGenContext,
) -> list[ast.stmt]:
    """Emit the assignment that puts each element the body produced back into the
    data it was taken from."""
    stmts: list[ast.stmt] = []
    for tile in tiles:
        element = _element_of(tile, elements)
        if tile.side != InPlaceBorderSide.WRITE or element is None:
            continue
        op = tile.op
        if tile.kind == _CLUSTER:
            stmts.extend(_write_fields_back(op, element, ctx))
            continue
        if tile.kind == _ARRAY:
            source = _role_terminal(op, "agg", "input")
            target = f"{element.whole}[{element.index}]"
        else:
            source = next(
                (t for t in op.terminals if t.direction == "input"),
                None,
            )
            target = f"{element.whole}[0]"
        value = ctx.wired_input(source.id, op) if source is not None else None
        if value is not None:
            stmts.append(_assign(parse_expr(target), value))
    return stmts


def _write_fields_back(
    op: PrimitiveNode, element: _Element, ctx: CodeGenContext
) -> list[ast.stmt]:
    """Emit data.field = modified_value for each field the WRITE tile takes."""
    agg_out = _role_terminal(op, "agg", "output")
    class_fields = _get_class_fields(agg_out, ctx) if agg_out else None
    stmts: list[ast.stmt] = []
    for t in _field_terminals(op, "input"):
        val = ctx.wired_input(t.id, op)
        if val is None:
            continue
        path = _field_path(t, class_fields, _class_attrs(agg_out, ctx))
        if path is None:
            continue
        stmts.append(_assign(_attribute_target(element.whole, path), val))
    return stmts


def _assign(target: ast.expr, value: str) -> ast.stmt:
    if isinstance(target, ast.Subscript | ast.Attribute):
        target.ctx = ast.Store()
    return ast.Assign(
        targets=[target],
        value=parse_expr(value),
        lineno=0,
        col_offset=0,
    )


def _bind_output_tunnels(
    node: InPlaceNode,
    tunnels: list[Tunnel],
    ctx: CodeGenContext,
    bindings: dict[str, str],
) -> None:
    """Propagate inner → outer for output-direction tunnels only."""
    outer_id_to_term = {t.id: t for t in node.terminals}
    for tunnel in tunnels:
        outer_term = outer_id_to_term.get(tunnel.outer_terminal_uid)
        if not outer_term or outer_term.direction != "output":
            continue
        inner_var = ctx.resolve(tunnel.inner_terminal_uid)
        if inner_var:
            bindings[tunnel.outer_terminal_uid] = inner_var


def _bind_data_outputs(
    node: InPlaceNode,
    producers: list[AnyGraphNode],
    data_var: str | None,
    tunnel_uids: set[str],
    bindings: dict[str, str],
    ctx: CodeGenContext,
) -> None:
    """Bind each output port that carries data to the value of the output of a
    tile or inner node that carries the same data (same ``dco_uid``). A port with
    no data-flow id is bound to the structure's one data variable.

    These ports have no graph edges (the connection to the tile is implicit), so
    a search from a consumer cannot find the data without this binding.
    """
    for port in node.terminals:
        if port.direction != "output" or port.id in tunnel_uids:
            continue
        if port.dco_uid is None:
            if data_var is not None:
                bindings[port.id] = data_var
            continue
        for producer in producers:
            source = next(
                (
                    t
                    for t in producer.terminals
                    if t.direction == "output" and t.dco_uid == port.dco_uid
                ),
                None,
            )
            if source is None:
                continue
            value = ctx.resolve(source.id)
            if value is not None:
                bindings[port.id] = value
            break


# ---------------------------------------------------------------------------
# Shared low-level helpers
# ---------------------------------------------------------------------------


def _find_data_var(
    node: InPlaceNode, tunnel_uids: set[str], ctx: CodeGenContext
) -> str | None:
    """The variable of the structure's one data input: the first input port that
    is not a tunnel and carries a value. None when there is none."""
    for t in node.terminals:
        if t.direction == "input" and t.id not in tunnel_uids:
            var = ctx.wired_input(t.id, node)
            if var:
                return var
    return None


def _role_terminal(
    op: PrimitiveNode, role: str | None, direction: str
) -> Terminal | None:
    """The tile's terminal of an aggregate/field role in a direction."""
    for t in op.terminals:
        if t.nmux_role == role and t.direction == direction:
            return t
    return None


def _carried_outputs(tile: _Tile) -> list[Terminal]:
    """The outputs of a WRITE tile that hand the edited data back out: the
    aggregate of a cluster tile, the array of an array tile, the reference of a
    data value reference tile."""
    if tile.kind == _CLUSTER:
        return [
            t
            for t in tile.op.terminals
            if t.nmux_role == "agg" and t.direction == "output"
        ]
    if tile.kind == _ARRAY:
        return [
            t
            for t in tile.op.terminals
            if t.nmux_role is None and t.direction == "output"
        ]
    return _data_terminals(tile.op, "output")


def _data_terminals(op: PrimitiveNode, direction: str) -> list[Terminal]:
    """A data value reference tile's terminals in ``direction`` that carry data,
    not an error."""
    return [
        t for t in op.terminals if t.direction == direction and not t.is_error_cluster
    ]


def _field_terminals(op: PrimitiveNode, direction: str) -> list[Terminal]:
    """Get list-role field terminals for the given direction, sorted by index."""
    return sorted(
        [t for t in op.terminals if t.nmux_role == "list" and t.direction == direction],
        key=lambda t: t.index,
    )


def _class_attrs(
    term: Terminal | None,
    ctx: CodeGenContext,
) -> ClassAttributes | None:
    """The attributes of the class an aggregate terminal carries."""
    if term is None or ctx.graph is None:
        return None
    return type_attributes(ctx.graph, term.lv_type, ctx.vi_name)


def _get_class_fields(
    term: Terminal | None,
    ctx: CodeGenContext,
) -> list[ClusterField] | None:
    """Get type fields for an aggregate terminal."""
    if term is None or term.lv_type is None or ctx.graph is None:
        return None
    return ctx.graph.get_type_fields(term.lv_type, caller_vi_key=ctx.vi_name)
