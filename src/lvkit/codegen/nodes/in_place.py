"""Code generator for In Place Element Structure (decompose/recompose).

An IPES takes one piece of data in, decomposes it at the input boundary
(creating field access expressions), lets inner operations modify those
fields, then recomposes at the output boundary (writing fields back).
Same data, no copies — works for clusters, arrays, classes, and DVRs.

generate() is structured as three explicit passes:
  Input boundary  — bind input tunnels + decompose field bindings
  Body            — generate_body(inner_nodes) for regular inner ops
  Output boundary — recompose write-backs + bind output tunnels + data output fallback
"""

from __future__ import annotations

import ast

from lvkit.graph.models import AnyGraphNode, InPlaceNode, PrimitiveNode
from lvkit.models import (
    ClusterField,
    Terminal,
    Tunnel,
)

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .nmux import _field_expr, _field_name


def _classify_ipes(
    children: list[AnyGraphNode],
    ctx: CodeGenContext,
) -> tuple[list[PrimitiveNode], list[PrimitiveNode], list[AnyGraphNode]]:
    """Split IPES inner graph nodes into decompose, recompose, and regular.

    decompose = poser prim with list OUTPUT terminals only (unbundles field
    values at the input boundary); recompose = poser prim with list INPUT
    terminals only (rebundles at the output boundary); everything else is
    regular body. ``poser_uid`` lives as a graph attribute, read via
    ``ctx.poser_uid``.
    """
    decompose: list[PrimitiveNode] = []
    recompose: list[PrimitiveNode] = []
    regular: list[AnyGraphNode] = []

    for op in children:
        if not isinstance(op, PrimitiveNode) or not ctx.poser_uid(op):
            regular.append(op)
            continue
        has_list_out = any(
            t.nmux_role == "list" and t.direction == "output" for t in op.terminals
        )
        has_list_in = any(
            t.nmux_role == "list" and t.direction == "input" for t in op.terminals
        )
        if has_list_out and not has_list_in:
            decompose.append(op)
        elif has_list_in and not has_list_out:
            recompose.append(op)
        else:
            regular.append(op)

    return decompose, recompose, regular


def generate(node: InPlaceNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for an In Place Element Structure."""
    all_stmts: list[ast.stmt] = []
    all_bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    tunnels = ctx.tunnels(node)
    decompose_ops, recompose_ops, inner_nodes = _classify_ipes(
        ctx.child_nodes(node), ctx
    )

    # --- Input boundary ---
    _bind_input_tunnels(node, tunnels, ctx)

    # Find the ONE data variable flowing through this IPES.
    # Same data in and out — no copies.
    tunnel_outer_uids = {t.outer_terminal_uid for t in tunnels}
    data_var = _find_data_var(node, tunnel_outer_uids, ctx)

    # Decompose: bind field output terminals to data.field expressions.
    _bind_decompose_fields(decompose_ops, data_var, ctx)

    # Pre-bind recompose agg outputs to the data variable so BFS
    # from parent structures can find the (same) data through recompose.
    _prebind_recompose_agg(recompose_ops, data_var, ctx)

    # --- Body (regular inner ops only) ---
    body_stmts = ctx.generate_body(inner_nodes)
    for stmt in body_stmts:
        ast.fix_missing_locations(stmt)
    all_stmts.extend(body_stmts)
    all_imports.update(ctx.imports)

    # --- Output boundary ---
    # Recompose (special output boundary): emit data.field = modified_value.
    all_stmts.extend(_emit_recompose_writebacks(recompose_ops, data_var, ctx))

    # Regular field-value tunnels: inner → outer (output direction only).
    _bind_output_tunnels(node, tunnels, ctx, all_bindings)

    # Cluster output terminals (decomposeClusterDCO) have no graph edges —
    # LabVIEW's implicit connection. Bind them to the data variable so
    # parent structures can resolve the modified data via BFS.
    _bind_data_outputs(node, tunnels, data_var, all_bindings)

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


def _bind_decompose_fields(
    decompose_ops: list[PrimitiveNode],
    data_var: str | None,
    ctx: CodeGenContext,
) -> None:
    """Bind decompose field output terminals to data.field expressions."""
    if data_var is None:
        return
    for op in decompose_ops:
        agg_in = _agg_terminal(op, "input")
        if agg_in is None:
            continue
        class_fields = _get_class_fields(agg_in, ctx)
        for t in _field_terminals(op, "output"):
            ctx.bind(t.id, _field_expr(t, data_var, class_fields))


def _prebind_recompose_agg(
    recompose_ops: list[PrimitiveNode],
    data_var: str | None,
    ctx: CodeGenContext,
) -> None:
    """Pre-bind recompose agg output terminals to the data variable.

    Same data, no copies: the recompose agg output IS the same data
    as the decompose agg input.
    """
    if data_var is None:
        return
    for op in recompose_ops:
        agg_out = _agg_terminal(op, "output")
        if agg_out:
            ctx.bind(agg_out.id, data_var)


# ---------------------------------------------------------------------------
# Output boundary helpers
# ---------------------------------------------------------------------------


def _emit_recompose_writebacks(
    recompose_ops: list[PrimitiveNode],
    data_var: str | None,
    ctx: CodeGenContext,
) -> list[ast.stmt]:
    """Emit data.field = modified_value for each recompose field."""
    if data_var is None:
        return []
    stmts: list[ast.stmt] = []
    for op in recompose_ops:
        agg_out = _agg_terminal(op, "output")
        class_fields = _get_class_fields(agg_out, ctx) if agg_out else None
        for t in _field_terminals(op, "input"):
            val = ctx.resolve(t.id)
            if val is None:
                continue
            fname = _field_name(t, class_fields)
            if fname is None:
                continue
            stmts.append(
                ast.Assign(
                    targets=[
                        ast.Attribute(
                            value=parse_expr(data_var),
                            attr=fname,
                            ctx=ast.Store(),
                        ),
                    ],
                    value=parse_expr(val),
                    lineno=0,
                    col_offset=0,
                )
            )
    return stmts


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
    tunnels: list[Tunnel],
    data_var: str | None,
    bindings: dict[str, str],
) -> None:
    """Bind IPES output terminals to the data variable.

    The decomposeClusterDCO output terminals have no graph edges (implicit
    LabVIEW connection), so BFS cannot find the data without explicit binding.
    """
    if data_var is None:
        return
    tunnel_inner_uids = {t.inner_terminal_uid for t in tunnels}
    tunnel_outer_uids = {t.outer_terminal_uid for t in tunnels}
    for t in node.terminals:
        if (
            t.direction == "output"
            and t.id not in tunnel_inner_uids
            and t.id not in tunnel_outer_uids
        ):
            bindings[t.id] = data_var


# ---------------------------------------------------------------------------
# Shared low-level helpers
# ---------------------------------------------------------------------------


def _find_data_var(
    node: InPlaceNode,
    tunnel_outer_uids: set[str],
    ctx: CodeGenContext,
) -> str | None:
    """Find the data variable from non-tunnel IPES input terminals.

    The data flows into the IPES via decomposeClusterDCO terminals, stored
    as plain input terminals on the node (not in node.tunnels).
    If no wired input exists, returns None and the IPES is a no-op.
    """
    for t in node.terminals:
        if t.direction == "input" and t.id not in tunnel_outer_uids:
            var = ctx.resolve(t.id)
            if var:
                return var
    return None


def _agg_terminal(op: PrimitiveNode, direction: str) -> Terminal | None:
    """Find the aggregate terminal for the given direction."""
    for t in op.terminals:
        if t.nmux_role == "agg" and t.direction == direction:
            return t
    return None


def _field_terminals(op: PrimitiveNode, direction: str) -> list[Terminal]:
    """Get list-role field terminals for the given direction, sorted by index."""
    return sorted(
        [t for t in op.terminals if t.nmux_role == "list" and t.direction == direction],
        key=lambda t: t.index,
    )


def _get_class_fields(
    term: Terminal | None,
    ctx: CodeGenContext,
) -> list[ClusterField] | None:
    """Get type fields for an aggregate terminal."""
    if term is None or term.lv_type is None or ctx.graph is None:
        return None
    return ctx.graph.get_type_fields(term.lv_type, caller_vi_key=ctx.vi_name)
