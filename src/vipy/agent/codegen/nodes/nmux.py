"""Code generator for Node Multiplexer (nMux) nodes.

nMux is bundle/unbundle at structure boundaries. Field resolution
uses the <i> field index from XML + dep_graph class fields. No heuristics.
"""

from __future__ import annotations

import ast

from vipy.graph_types import (
    ClusterField,
    PrimitiveOperation,
    Terminal,
    TypeResolutionNeeded,
    _is_error_cluster,
)

from ..ast_utils import parse_expr, to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment


def generate(node: PrimitiveOperation, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for nMux (bundle/unbundle at structure boundaries).

    Terminals have roles set by graph construction:
    - agg: the aggregate cluster/class wire (passthrough)
    - list: individual field values (each has nmux_field_index from <i> in XML)
    """
    def _by_role(direction: str, role: str) -> list[Terminal]:
        return [
            t for t in node.terminals
            if t.direction == direction and t.nmux_role == role
        ]

    agg_in = _by_role("input", "agg")
    agg_out = _by_role("output", "agg")
    list_in = _by_role("input", "list")
    list_out = _by_role("output", "list")

    bindings: dict[str, str] = {}
    statements: list[ast.stmt] = []

    # Resolve AGG input variable
    agg_var = None
    if agg_in:
        agg_var = ctx.resolve(agg_in[0].id)

    # AGG passthrough: bind agg outputs to agg input value
    for t in agg_out:
        if agg_var:
            bindings[t.id] = agg_var

    # Get fields for field index lookup.
    class_fields = None
    agg_terminals = agg_in or agg_out
    if agg_terminals and agg_terminals[0].lv_type:
        if ctx.graph is not None:
            class_fields = ctx.graph.get_type_fields(
                agg_terminals[0].lv_type,
            )

    if list_in and list_out:
        # LIST in + LIST out = passthrough at structure boundary
        sorted_in = sorted(list_in, key=lambda t: t.index)
        sorted_out = sorted(list_out, key=lambda t: t.index)
        for i, t_out in enumerate(sorted_out):
            if i < len(sorted_in):
                val = ctx.resolve(sorted_in[i].id)
                if val:
                    bindings[t_out.id] = val

    elif list_out and not list_in:
        # LIST out only = unbundle (extract fields from cluster)
        for t in sorted(list_out, key=lambda t: t.index):
            if agg_var:
                expr = _field_expr(t, agg_var, class_fields)
                bindings[t.id] = expr

    elif list_in and not list_out:
        # LIST in only = bundle (assign fields on cluster)
        if (
            agg_terminals
            and agg_terminals[0].lv_type
            and _is_error_cluster(agg_terminals[0].lv_type)
        ):
            if _bundles_status(list_in, class_fields):
                return _generate_error_bundle(list_in, class_fields, ctx)
            return CodeFragment.empty()

        if agg_var:
            for t in sorted(list_in, key=lambda t: t.index):
                val = ctx.resolve(t.id)
                if not val:
                    continue
                fname = _field_name(t, class_fields)
                if fname:
                    stmt = ast.Assign(
                        targets=[ast.Attribute(
                            value=parse_expr(agg_var),
                            attr=fname,
                            ctx=ast.Store(),
                        )],
                        value=parse_expr(val),
                    )
                    statements.append(stmt)
                else:
                    raise TypeResolutionNeeded(
                        type_name=f"field[{t.nmux_field_index}]",
                        context=t.id,
                    )

    return CodeFragment(statements=statements, bindings=bindings)


def _bundles_status(
    list_in: list[Terminal],
    class_fields: list[ClusterField] | None,
) -> bool:
    """Check if any LIST input maps to the 'status' field."""
    if not class_fields:
        return False
    for t in list_in:
        if t.nmux_field_index is not None:
            if t.nmux_field_index < len(class_fields):
                if to_var_name(
                    class_fields[t.nmux_field_index].name,
                ) == "status":
                    return True
    return False


def _generate_error_bundle(
    list_in: list[Terminal],
    class_fields: list[ClusterField] | None,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate conditional raise for error cluster bundle."""
    field_values: dict[str, str] = {}
    for t in sorted(list_in, key=lambda t: t.index):
        val = ctx.resolve(t.id)
        if not val:
            continue
        fname = _field_name(t, class_fields)
        if fname:
            field_values[fname] = val

    status_val = field_values.get("status", "True")
    code_val = field_values.get("code", "0")
    source_val = field_values.get("source", "''")

    raise_stmt = ast.Raise(
        exc=ast.Call(
            func=ast.Name(id="LabVIEWError", ctx=ast.Load()),
            args=[],
            keywords=[
                ast.keyword(arg="code", value=parse_expr(code_val)),
                ast.keyword(arg="source", value=parse_expr(source_val)),
            ],
        ),
        cause=None,
    )
    if_stmt = ast.If(
        test=parse_expr(status_val),
        body=[raise_stmt],
        orelse=[],
    )

    imports = {"from vipy.labview_error import LabVIEWError"}
    return CodeFragment(statements=[if_stmt], bindings={}, imports=imports)


def _flatten_fields(
    fields: list[ClusterField],
) -> list[tuple[list[str], ClusterField]]:
    """Flatten cluster fields depth-first with path.

    LabVIEW nMux <i> tags use flattened indices across the entire
    cluster hierarchy, not just the top level.
    """
    result: list[tuple[list[str], ClusterField]] = []
    for f in fields:
        result.append(([f.name], f))
        if f.type and f.type.fields:
            for sub_path, sub_field in _flatten_fields(f.type.fields):
                result.append(([f.name] + sub_path, sub_field))
    return result


def _field_name(
    term: Terminal, class_fields: list[ClusterField] | None,
) -> str | None:
    """Get Python field name for a LIST terminal using nmux_field_index."""
    if term.nmux_field_index is None or not class_fields:
        return None
    if term.nmux_field_index < len(class_fields):
        return to_var_name(class_fields[term.nmux_field_index].name)
    flat = _flatten_fields(class_fields)
    if term.nmux_field_index < len(flat):
        path, _field = flat[term.nmux_field_index]
        return to_var_name(path[-1])
    return None


def _field_expr(
    term: Terminal, agg_var: str,
    class_fields: list[ClusterField] | None,
) -> str:
    """Resolve field expression for a LIST output terminal (unbundle)."""
    if term.nmux_field_index is not None and class_fields:
        if term.nmux_field_index < len(class_fields):
            fname = to_var_name(class_fields[term.nmux_field_index].name)
            return f"{agg_var}.{fname}"
        flat = _flatten_fields(class_fields)
        if term.nmux_field_index < len(flat):
            path, _field = flat[term.nmux_field_index]
            dotted = ".".join(to_var_name(p) for p in path)
            return f"{agg_var}.{dotted}"

    raise TypeResolutionNeeded(
        type_name=f"field[{term.nmux_field_index}]",
        context=term.id,
    )
