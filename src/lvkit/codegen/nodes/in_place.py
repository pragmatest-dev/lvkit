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
from dataclasses import dataclass, field

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import AnyGraphNode, InPlaceNode, PrimitiveNode
from lvkit.models import (
    MAP_ACCESS_NODE_TYPE,
    ClusterField,
    InPlaceBorderSide,
    Terminal,
    Tunnel,
    inplace_border_side,
)

from .. import error_flow
from ..ast_utils import build_assign, parse_expr
from ..class_attributes import ClassAttributes
from ..class_model import type_attributes
from ..cluster_members import ClusterShape, is_anonymous_cluster
from ..context import CodeGenContext
from ..errors_module import LABVIEW_ERROR_NAME
from ..fragment import CodeFragment
from ..native_array import guarded_indexes, replaced_elements
from ..type_default import type_default
from ..value_copy import owned_input
from . import in_place_map, waveform_bundle
from .nmux import _write_elements, element_read

_CLUSTER = "decomposeClusterNode"
_ARRAY = "decomposeArrayNode"
_DVR = "decomposeDataValRefNode"
_MAP = MAP_ACCESS_NODE_TYPE
# A whole-value tile: no field split, the value enters and leaves unchanged.
_WHOLE = "decomposeMatchNode"
_BORDER_NODE_TYPES = (_CLUSTER, _ARRAY, _DVR, _MAP)


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
    and, for an array, the index variables of each element it takes (one set per
    element, one index per dimension in a set, outermost first; a map's one set
    is its key)."""

    whole: str
    index_sets: list[list[str]] = field(default_factory=list)


def _split_children(
    children: list[AnyGraphNode], ctx: CodeGenContext
) -> tuple[list[_Tile], list[PrimitiveNode], list[AnyGraphNode]]:
    """Split the structure's inner graph nodes into border tiles, whole-value
    tiles and regular body nodes. A cluster tile is a node with a pairing id and
    field terminals in one direction only; an array, data value reference or
    whole-value tile is told by its node type."""
    tiles: list[_Tile] = []
    whole: list[PrimitiveNode] = []
    regular: list[AnyGraphNode] = []
    for op in children:
        if isinstance(op, PrimitiveNode) and op.node_type == _WHOLE:
            whole.append(op)
            continue
        node_type = None
        side = None
        if isinstance(op, PrimitiveNode):
            if op.node_type in (_ARRAY, _DVR, _MAP):
                node_type = op.node_type
            elif ctx.poser_uid(op):
                node_type = _CLUSTER
            if node_type is not None:
                side = inplace_border_side(node_type, op.terminals)
        if isinstance(op, PrimitiveNode) and node_type is not None and side is not None:
            tiles.append(_Tile(op, side, node_type, (node_type, ctx.poser_uid(op))))
        else:
            regular.append(op)
    return tiles, whole, regular


def generate(node: InPlaceNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for an In Place Element Structure."""
    all_stmts: list[ast.stmt] = []
    all_bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    tunnels = ctx.tunnels(node)
    tiles, whole_tiles, inner_nodes = _split_children(ctx.child_nodes(node), ctx)

    # --- Input boundary ---
    _bind_input_tunnels(node, tunnels, ctx)
    tunnel_uids = {
        uid for t in tunnels for uid in (t.outer_terminal_uid, t.inner_terminal_uid)
    }
    _own_data_inputs(node, tunnel_uids, ctx, all_stmts)
    _bind_tile_inputs(node, tiles, ctx)
    data_var = _find_data_var(node, tunnel_uids, ctx)
    elements = _take_elements_out(tiles, data_var, ctx)
    # A border tile's error out is the error that comes in on it, else none.
    for tile in tiles:
        ctx.merge(error_flow.guard(tile.op, CodeFragment(), ctx).bindings)
    _invalid_reference_errors(tiles, elements, ctx)

    _pass_whole_values(node, whole_tiles, ctx)

    # --- Body (regular inner ops only) ---
    body_stmts = ctx.generate_body(inner_nodes, (node.id, None))
    for stmt in body_stmts:
        ast.fix_missing_locations(stmt)
    all_stmts.extend(body_stmts)
    all_imports.update(ctx.imports)

    # --- Output boundary ---
    _pass_whole_values(node, whole_tiles, ctx)
    all_stmts.extend(_write_elements_back(tiles, elements, ctx))

    # Regular field-value tunnels: inner → outer (output direction only).
    _bind_output_tunnels(node, tunnels, ctx, all_bindings)

    # The data comes back out of the structure on its output ports.
    producers = [*(t.op for t in tiles), *whole_tiles, *inner_nodes]
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
        elif (
            value is not None
            and not value.isidentifier()
            and is_anonymous_cluster(port.lv_type)
        ):
            # A tuple written into is rebuilt and stored back, so the cluster
            # needs a variable to hold the changed one.
            var = ctx.make_output_var("cluster", node.id, port.id)
            statements.append(build_assign(var, parse_expr(value)))
            ctx.bind(port.id, var)


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


def _pass_whole_values(
    node: InPlaceNode, whole_tiles: list[PrimitiveNode], ctx: CodeGenContext
) -> None:
    """Bind the output of each whole-value tile to the value on its input: that
    of the wire feeding it, else of the structure input port that carries the same
    data (same ``dco_uid``). A tile whose input has no value yet is left for the
    next call, once the body has produced it."""
    ports = [t for t in node.terminals if t.direction == "input"]
    for op in whole_tiles:
        source = next((t for t in op.terminals if t.direction == "input"), None)
        target = next((t for t in op.terminals if t.direction == "output"), None)
        if source is None or target is None:
            continue
        if ctx.resolve(target.id, allow_none=True) is not None:
            continue
        value = ctx.resolve(source.id, allow_none=True)
        if value is None and source.dco_uid is not None:
            for port in ports:
                if port.dco_uid == source.dco_uid:
                    value = ctx.resolve(port.id, allow_none=True)
                    break
        if value is not None:
            ctx.bind(target.id, value)


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
        shape = _cluster_shape(agg, ctx)
        flavor = agg.lv_type.measure_flavor if agg.lv_type else None
        for t in _field_terminals(op, "output"):
            if flavor:
                if ctx.is_wired(t.id):
                    ctx.bind(
                        t.id, waveform_bundle.component_read(op, t, whole, flavor, ctx)
                    )
                continue
            read = element_read(op, t, whole, shape, ctx)
            if read is not None:
                ctx.bind(t.id, read)
        return _Element(whole)
    if tile.kind == _ARRAY:
        return _take_array_elements_out(op, ctx)
    if tile.kind == _MAP:
        access = in_place_map.read_tile(op, ctx)
        return _Element(access[0], [[access[1]]]) if access is not None else None
    dvr_in = next(
        (t for t in op.terminals if t.direction == "input"),
        None,
    )
    value_out = next(iter(_data_terminals(op, "output")), None)
    whole = ctx.wired_input(dvr_in.id, op) if dvr_in is not None else None
    if whole is None or value_out is None:
        return None
    # A reference that is Not a Refnum (a class's private data holding a default
    # reference) reads the data type's default and returns error 1556 "The
    # reference is invalid" (the code LabVIEW reports for an invalid reference;
    # NI's forum threads on DVR In Place Element Structure errors).
    default = ast.unparse(type_default(ctx, value_out.lv_type, "A DVR value of"))
    ctx.bind(value_out.id, f"({whole}[0] if {whole} is not None else {default})")
    return _Element(whole)


def _invalid_reference_errors(
    tiles: list[_Tile],
    elements: dict[tuple[str, str | None], _Element],
    ctx: CodeGenContext,
) -> None:
    """A data value reference tile's error out, once it holds the error that came
    in: error 1556 when the reference it reads is Not a Refnum and no error came
    in."""
    for tile in tiles:
        element = elements.get(tile.pair)
        if tile.kind != _DVR or tile.side != InPlaceBorderSide.READ or element is None:
            continue
        error_out = next(
            (
                t
                for t in tile.op.terminals
                if t.direction == "output" and t.is_error_cluster
            ),
            None,
        )
        if error_out is None:
            continue
        incoming = ctx.resolve(error_out.id, allow_none=True) or "None"
        ctx.import_labview_error()
        invalid = (
            f"{LABVIEW_ERROR_NAME}(code=1556, "
            "source='In Place Element Structure', "
            "message='The reference is invalid.')"
        )
        if incoming == "None":
            expression = f"(None if {element.whole} is not None else {invalid})"
        else:
            expression = (
                f"({incoming} if {incoming} is not None or "
                f"{element.whole} is not None else {invalid})"
            )
        ctx.bind(error_out.id, expression)


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
            stmts.extend(_write_fields_back(tile, element, ctx))
            continue
        if tile.kind == _ARRAY:
            stmts.extend(_write_array_element_back(tile, element, ctx))
            continue
        if tile.kind == _MAP:
            stmts.extend(
                in_place_map.write_tile(
                    op, element.whole, element.index_sets[0][0], ctx
                )
            )
            continue
        source = next(
            (t for t in op.terminals if t.direction == "input"),
            None,
        )
        value = ctx.wired_input(source.id, op) if source is not None else None
        target = f"{element.whole}[0]"
        # An element that went through the structure unchanged is still the
        # expression the read bound; writing it back changes nothing.
        if value is not None and value != target:
            stmts.append(
                ast.If(
                    test=parse_expr(f"{element.whole} is not None"),
                    body=[_assign(parse_expr(target), value)],
                    orelse=[],
                )
            )
    return stmts


def _take_array_elements_out(op: PrimitiveNode, ctx: CodeGenContext) -> _Element | None:
    """Bind each element output of an Index tile to the array element its indexes
    pick. NI ("Array Index / Replace Elements"): the border node "gains an
    additional index parameter for each additional dimension of the array", and
    its left half "is similar to the Index Array function": an element is
    whole[i][j]..., one index per dimension in terminal order, and a tile can
    take several elements, each with its own set of indexes. Fewer wired indexes
    than dimensions read the sub-array they select (one index of two dimensions,
    a row). An index outside its dimension reads the element type's default."""
    array_in = _role_terminal(op, None, "input")
    outputs = sorted(
        (
            t
            for t in op.terminals
            if t.nmux_role in ("agg", "list") and t.direction == "output"
        ),
        key=lambda t: t.index,
    )
    index_terms = _field_terminals(op, "input")
    if array_in is None or not outputs or not index_terms:
        return None
    whole = ctx.wired_input(array_in.id, op)
    if whole is None:
        return None
    rank = (array_in.lv_type.dimensions if array_in.lv_type else None) or 1
    if len(index_terms) != rank * len(outputs):
        raise ConversionGap(
            f"An In Place Element array tile with {len(index_terms)} indexes and "
            f"{len(outputs)} elements of a {rank}-dimensional array has no code "
            "generation yet",
            op,
        )
    sets: list[list[str]] = []
    for position, out in enumerate(outputs):
        indexes: list[str] = []
        unwired_before = False
        for term in index_terms[position * rank : (position + 1) * rank]:
            value = ctx.wired_input(term.id, op)
            if value is None:
                unwired_before = True
            elif unwired_before:
                raise ConversionGap(
                    "An In Place Element array tile with an index wired after one "
                    "that is not has no code generation yet",
                    op,
                )
            else:
                indexes.append(value)
        sets.append(indexes)
        if indexes:
            default = ast.unparse(type_default(ctx, out.lv_type))
            ctx.bind(out.id, guarded_indexes(whole, indexes, default))
        elif ctx.is_wired(out.id):
            raise ConversionGap(
                "An In Place Element array tile element whose output is wired and "
                "whose indexes are not has no code generation yet",
                op,
            )
    if not any(sets):
        return None
    return _Element(whole, sets)


def _write_array_element_back(
    tile: _Tile, element: _Element, ctx: CodeGenContext
) -> list[ast.stmt]:
    """Bind the tile's array output to a new array holding the elements the body
    produced, one replacement per element in order. The array that came in is not
    changed, so any other reader of its wire keeps its own value."""
    op = tile.op
    sources = sorted(
        (
            t
            for t in op.terminals
            if t.nmux_role in ("agg", "list") and t.direction == "input"
        ),
        key=lambda t: t.index,
    )
    outputs = _carried_outputs(tile)
    out = outputs[0] if outputs else None
    stmts: list[ast.stmt] = []
    current = element.whole
    for source, indexes in zip(sources, element.index_sets):
        value = ctx.wired_input(source.id, op)
        if value is None or not indexes:
            continue
        name = ctx.make_output_var("array", op.id, out.id if out else None)
        stmts.append(
            _assign(
                ast.Name(id=name, ctx=ast.Store()),
                replaced_elements(current, indexes, value),
            )
        )
        current = name
    if not stmts:
        return []
    for carried in outputs:
        ctx.bind(carried.id, current)
    return stmts


def _write_fields_back(
    tile: _Tile, element: _Element, ctx: CodeGenContext
) -> list[ast.stmt]:
    """Put each element the body produced back into the data: a field of a
    cluster is set where it is; a waveform is rebuilt with the components."""
    op = tile.op
    agg_out = _role_terminal(op, "agg", "output")
    fields = _field_terminals(op, "input")
    flavor = agg_out.lv_type.measure_flavor if agg_out and agg_out.lv_type else None
    if flavor and agg_out is not None:
        fragment = waveform_bundle.bundle(
            op, fields, [agg_out], element.whole, flavor, ctx
        )
        for statement_import in fragment.imports:
            ctx.add_import(statement_import)
        for carried in _carried_outputs(tile):
            ctx.bind(carried.id, fragment.bindings[agg_out.id])
        return fragment.statements
    statements: list[ast.stmt] = []
    imports: set[str] = set()
    _write_elements(
        op,
        fields,
        element.whole,
        _cluster_shape(agg_out, ctx),
        ctx,
        statements,
        imports,
    )
    for statement_import in imports:
        ctx.add_import(statement_import)
    return statements


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
            value = ctx.resolve(source.id, allow_none=True)
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
    if tile.kind == _MAP:
        out = in_place_map.map_terminal(tile.op, "output")
        return [out] if out is not None else []
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
    return type_attributes(ctx.graph, term.lv_type, ctx.vi_name, "In Place Element")


def _cluster_shape(term: Terminal | None, ctx: CodeGenContext) -> ClusterShape:
    """The type, fields and class attributes of the cluster an aggregate terminal
    carries."""
    return ClusterShape(
        term.lv_type if term else None,
        _get_class_fields(term, ctx),
        _class_attrs(term, ctx),
    )


def _get_class_fields(
    term: Terminal | None,
    ctx: CodeGenContext,
) -> list[ClusterField] | None:
    """Get type fields for an aggregate terminal."""
    if term is None or term.lv_type is None or ctx.graph is None:
        return None
    return ctx.graph.get_type_fields(term.lv_type)
