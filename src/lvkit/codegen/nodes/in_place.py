"""Code generator for In Place Element Structure (decompose/recompose).

An IPES decomposes a cluster/array/DVR into fields at entry, lets inner
operations modify those fields, then recomposes at exit. In Python, objects
are mutable references — the structure is transparent, reducing to field
access and in-place write-back (cluster.field = modified_value).
"""

from __future__ import annotations

import ast

from lvkit.models import (
    ClusterField,
    InPlaceOperation,
    PrimitiveOperation,
    Terminal,
)

from ..ast_utils import parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .nmux import _field_expr, _field_name


def generate(node: InPlaceOperation, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for an In Place Element Structure.

    Steps:
    1. Bind decomposeRecomposeTunnel inner terminals (field-value tunnels)
    2. Identify decompose/recompose inner op pairs (by poser_uid)
    3. For each decompose op: bind field output terminals to cluster.field exprs
    4. For each recompose op: pre-bind agg output to the cluster variable
    5. Execute regular inner operations
    6. For each recompose op: emit cluster.field = value assignment statements
    7. Bind output tunnels and cluster output terminals
    """
    all_stmts: list[ast.stmt] = []
    all_bindings: dict[str, str] = {}
    all_imports: set[str] = set()

    # 1. Bind decomposeRecomposeTunnel inner terminals to their outer values.
    # These tunnels carry FIELD VALUES (not the cluster itself) — do not use
    # them as the cluster variable fallback.
    tunnel_outer_uids = {t.outer_terminal_uid for t in node.tunnels}
    for tunnel in node.tunnels:
        outer_var = ctx.resolve(tunnel.outer_terminal_uid)
        if outer_var:
            ctx.bind(tunnel.inner_terminal_uid, outer_var)

    # 2. Classify inner ops
    decompose_ops, recompose_ops, regular_ops = _classify_inner_ops(
        node.inner_nodes,
    )

    # Maps poser_uid → cluster variable name (from the decompose side)
    cluster_vars: dict[str, str] = {}

    # 3. Process decompose ops: bind field output terminals to cluster.field
    for op in decompose_ops:
        agg_in = _agg_terminal(op, "input")
        if agg_in is None:
            continue
        # The decomposeClusterDCO creates an implicit (non-wire) link between
        # the cluster input terminal (467) and the decompose agg input (454).
        # No wire edge exists for agg_in, so ctx.resolve(agg_in.id) always
        # returns None. Find the cluster via the IPES's non-tunnel input
        # terminals, which ARE connected by normal wires from the outer scope.
        cluster_var = _find_cluster_var(node, tunnel_outer_uids, ctx)
        if cluster_var is None:
            continue
        if op.poser_uid:
            cluster_vars[op.poser_uid] = cluster_var

        class_fields = _get_class_fields(agg_in, ctx)
        for t in _field_terminals(op, "output"):
            expr = _field_expr(t, cluster_var, class_fields)
            ctx.bind(t.id, expr)

    # 4. Pre-bind recompose agg outputs to cluster variables
    for op in recompose_ops:
        cluster_var = cluster_vars.get(op.poser_uid or "")
        if cluster_var is None:
            continue
        agg_out = _agg_terminal(op, "output")
        if agg_out:
            ctx.bind(agg_out.id, cluster_var)

    # 5. Execute regular inner operations
    body_stmts = ctx.generate_body(regular_ops)
    for stmt in body_stmts:
        ast.fix_missing_locations(stmt)
    all_stmts.extend(body_stmts)
    all_imports.update(ctx.imports)

    # 6. Emit recompose write-backs (cluster.field = modified_value)
    for op in recompose_ops:
        cluster_var = cluster_vars.get(op.poser_uid or "")
        if cluster_var is None:
            continue
        agg_in = _agg_terminal(op, "input")
        agg_out = _agg_terminal(op, "output")
        agg_term = agg_in or agg_out
        class_fields = _get_class_fields(agg_term, ctx) if agg_term else None

        for t in _field_terminals(op, "input"):
            val = ctx.resolve(t.id)
            if val is None:
                continue
            fname = _field_name(t, class_fields)
            if fname is None:
                continue
            stmt = ast.Assign(
                targets=[
                    ast.Attribute(
                        value=parse_expr(cluster_var),
                        attr=fname,
                        ctx=ast.Store(),
                    ),
                ],
                value=parse_expr(val),
                lineno=0,
                col_offset=0,
            )
            all_stmts.append(stmt)

    # 7. Bind output tunnels and cluster output terminals.
    #
    # decomposeRecomposeTunnel types:
    #   Input tunnel  (outer.direction == "input"):  data flows outer → inner.
    #                 Step 1 already bound inner from outer.  Nothing to do here.
    #   Output tunnel (outer.direction == "output"): data flows inner → outer.
    #                 Resolve the inner value (produced by inner ops) and bind
    #                 the outer so the parent structure can read it via BFS.
    #
    # Cluster output terminals (decomposeClusterDCO, non-tunnel):
    #   No graph edge connects them to the inner ops (implicit LabVIEW connection),
    #   so BFS cannot find the cluster without an explicit binding.
    outer_id_to_term = {t.id: t for t in node.terminals}
    tunnel_inner_uids = {t.inner_terminal_uid for t in node.tunnels}
    fallback_cluster = next(iter(cluster_vars.values()), None)

    for tunnel in node.tunnels:
        outer_term = outer_id_to_term.get(tunnel.outer_terminal_uid)
        if outer_term and outer_term.direction != "output":
            # Input tunnel — skip (step 1 already propagated outer → inner).
            continue
        inner_var = ctx.resolve(tunnel.inner_terminal_uid)
        if inner_var:
            all_bindings[tunnel.outer_terminal_uid] = inner_var

    # Bind decomposeClusterDCO output terminals to the cluster variable so
    # parent structures can resolve the modified cluster via BFS.
    if fallback_cluster:
        for t in node.terminals:
            if (
                t.direction == "output"
                and t.id not in tunnel_inner_uids
                and t.id not in tunnel_outer_uids
            ):
                ctx.bind(t.id, fallback_cluster)
                all_bindings[t.id] = fallback_cluster

    return CodeFragment(
        statements=all_stmts,
        bindings=all_bindings,
        imports=all_imports,
    )


def _find_cluster_var(
    node: InPlaceOperation,
    tunnel_outer_uids: set[str],
    ctx: CodeGenContext,
) -> str | None:
    """Find the cluster variable from non-tunnel IPES input terminals.

    The cluster flows into the IPES via decomposeClusterDCO terminals, which
    are stored as regular input terminals on the InPlaceOperation (not in
    node.tunnels, which only tracks decomposeRecomposeTunnel terminals).
    """
    for t in node.terminals:
        if t.direction == "input" and t.id not in tunnel_outer_uids:
            var = ctx.resolve(t.id)
            if var:
                return var
    return None


def _classify_inner_ops(
    inner_nodes: list,
) -> tuple[list[PrimitiveOperation], list[PrimitiveOperation], list]:
    """Split inner ops into decompose, recompose, and regular.

    Decompose ops: PrimitiveOperation with poser_uid and list OUTPUT terminals.
    Recompose ops: PrimitiveOperation with poser_uid and list INPUT terminals.
    Regular ops: everything else (pass to generate_body as normal).
    """
    decompose: list[PrimitiveOperation] = []
    recompose: list[PrimitiveOperation] = []
    regular = []

    for op in inner_nodes:
        if not isinstance(op, PrimitiveOperation) or not op.poser_uid:
            regular.append(op)
            continue
        has_list_out = any(
            t.nmux_role == "list" and t.direction == "output"
            for t in op.terminals
        )
        has_list_in = any(
            t.nmux_role == "list" and t.direction == "input"
            for t in op.terminals
        )
        if has_list_out and not has_list_in:
            decompose.append(op)
        elif has_list_in and not has_list_out:
            recompose.append(op)
        else:
            # Passthrough case or unknown — treat as regular
            regular.append(op)

    return decompose, recompose, regular


def _agg_terminal(op: PrimitiveOperation, direction: str) -> Terminal | None:
    """Find the aggregate (cluster) terminal for the given direction."""
    for t in op.terminals:
        if t.nmux_role == "agg" and t.direction == direction:
            return t
    return None


def _field_terminals(op: PrimitiveOperation, direction: str) -> list[Terminal]:
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
    return ctx.graph.get_type_fields(term.lv_type)
