"""Code generator for Node Multiplexer (nMux) nodes.

nMux is bundle/unbundle at structure boundaries. Field resolution
uses the <i> field index from XML + dep_graph class fields. No heuristics.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import _flatten_fields
from lvkit.lv_type_codes import is_boolean_type_name, is_string_type_name
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    Terminal,
    TypeResolutionNeeded,
    _is_error_cluster,
)

from ..ast_utils import (
    build_assign,
    build_assign_attr,
    default_value_expr,
    parse_expr,
    to_var_name,
)
from ..context import CodeGenContext
from ..elementwise import LV_IMPORT
from ..fragment import CodeFragment


def _is_anonymous_cluster(lv_type: LVType | None) -> bool:
    """True for an on-diagram anonymous cluster — no typedef and no class, so
    its fields are positional placeholders (e.g. ``field_1``) with no meaningful
    names. Such a cluster is represented as a Python tuple accessed by position;
    a named/typedef cluster keeps attribute access. See
    docs/_internal/design/codegen-fix-assumptions.md."""
    return (
        lv_type is not None
        and lv_type.kind == LVTypeKind.CLUSTER
        and not lv_type.typedef_name
        and not lv_type.classname
    )


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for nMux (bundle/unbundle at structure boundaries).

    Terminals have roles set by graph construction:
    - agg: the aggregate cluster/class wire (passthrough)
    - list: individual field values (each has nmux_field_index from <i> in XML)
    """

    def _by_role(direction: str, role: str) -> list[Terminal]:
        return [
            t
            for t in node.terminals
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
    agg_lv_type = agg_terminals[0].lv_type if agg_terminals else None
    anon = _is_anonymous_cluster(agg_lv_type)
    if agg_terminals and agg_terminals[0].lv_type:
        if ctx.graph is not None:
            class_fields = ctx.graph.get_type_fields(
                agg_terminals[0].lv_type,
                caller_vi_key=ctx.vi_name,
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
            if agg_lv_type is not None and _is_error_cluster(agg_lv_type):
                read = _error_read(t, class_fields)
                if read is not None:
                    bindings[t.id] = (
                        read.caught_value(ctx.error_var)
                        if ctx.error_var
                        else read.no_error_value
                    )
            elif agg_var:
                if anon:
                    path = _position_path(t, class_fields)
                    bindings[t.id] = f"({agg_var})" + "".join(
                        f"[{index}]" for index in path
                    )
                else:
                    bindings[t.id] = _field_expr(t, agg_var, class_fields)

    elif list_in and not list_out:
        # LIST in only = bundle
        if (
            agg_terminals
            and agg_terminals[0].lv_type
            and _is_error_cluster(agg_terminals[0].lv_type)
        ):
            return _generate_error_bundle(list_in, class_fields, ctx)

        if agg_var:
            if anon:
                updates: list[ast.expr] = []
                for t in sorted(list_in, key=lambda t: t.index):
                    value = ctx.resolve(t.id)
                    if value is None:
                        raise TypeResolutionNeeded(
                            type_name="bundle value", context=t.id
                        )
                    path = _position_path(t, class_fields)
                    updates.append(
                        ast.Tuple(
                            elts=[parse_expr(repr(path)), parse_expr(value)],
                            ctx=ast.Load(),
                        )
                    )
                cluster_var = ctx.make_output_var(
                    "cluster", node.id, agg_out[0].id if agg_out else None
                )
                statements.append(
                    build_assign(
                        cluster_var,
                        ast.Call(
                            func=ast.Attribute(
                                value=ast.Name(id="_lv", ctx=ast.Load()),
                                attr="replace_fields",
                                ctx=ast.Load(),
                            ),
                            args=[
                                parse_expr(agg_var),
                                ast.Tuple(elts=updates, ctx=ast.Load()),
                            ],
                            keywords=[],
                        ),
                    )
                )
                for t in agg_out:
                    bindings[t.id] = cluster_var
                return CodeFragment(
                    statements=statements,
                    bindings=bindings,
                    imports={LV_IMPORT},
                )
            # Assign fields on the aggregate cluster. Bundle By Name mutates the
            # object in place, so the aggregate must be a single materialized
            # VARIABLE — when it resolves to an EXPRESSION (a cluster constant
            # inlined as `SimpleNamespace(...)`), each `.field =` would otherwise
            # hit a throwaway object and the mutations would be lost. Bind it to
            # one variable, then mutate and pass that on.
            if not agg_var.isidentifier():
                cluster_var = ctx.make_output_var(
                    "cluster", node.id, agg_out[0].id if agg_out else None
                )
                statements.append(
                    ast.Assign(
                        targets=[ast.Name(id=cluster_var, ctx=ast.Store())],
                        value=parse_expr(agg_var),
                    )
                )
                agg_var = cluster_var
                for t in agg_out:
                    bindings[t.id] = cluster_var
            # Assign fields on an EXISTING cluster (incoming agg wire).
            for t in sorted(list_in, key=lambda t: t.index):
                val = ctx.resolve(t.id)
                if not val:
                    continue
                fname = _field_name(t, class_fields)
                if fname:
                    stmt = ast.Assign(
                        targets=[
                            ast.Attribute(
                                value=parse_expr(agg_var),
                                attr=fname,
                                ctx=ast.Store(),
                            )
                        ],
                        value=parse_expr(val),
                    )
                    statements.append(stmt)
                else:
                    raise TypeResolutionNeeded(
                        type_name=f"field[{t.nmux_field_index}]",
                        context=t.id,
                    )
        elif anon and agg_out:
            # CREATE a new anonymous cluster from the field inputs (a classic
            # Bundle with no incoming cluster wire) as a positional tuple, bound
            # to the agg output. Fields go in cluster-field-index order.
            ordered = sorted(
                list_in,
                key=lambda t: (
                    t.nmux_field_index if t.nmux_field_index is not None else t.index
                ),
            )
            elts = [parse_expr(ctx.resolve(t.id) or "None") for t in ordered]
            tup = ast.Tuple(elts=elts, ctx=ast.Load())
            for t in agg_out:
                bindings[t.id] = ast.unparse(tup)

    return CodeFragment(statements=statements, bindings=bindings)


class ErrorFieldRole(Enum):
    """Which field of an error cluster a slot is."""

    STATUS = "status"
    CODE = "code"
    SOURCE = "source"


def error_field_role(field_type: LVType | None) -> ErrorFieldRole:
    """The role of an error cluster field, from its type: the Boolean is the
    status, the string the source, the number the code."""
    underlying = field_type.underlying_type if field_type else None
    if is_boolean_type_name(underlying):
        return ErrorFieldRole.STATUS
    if is_string_type_name(underlying):
        return ErrorFieldRole.SOURCE
    return ErrorFieldRole.CODE


@dataclass(frozen=True)
class ErrorFieldRead:
    """One field an Unbundle takes out of an error cluster: the terminal it
    leaves from, its role, and the value it holds when no error occurred (the
    type's default: status False, code 0, source empty)."""

    terminal_id: str
    role: ErrorFieldRole
    no_error_value: str

    def caught_value(self, error_var: str) -> str:
        """The value of the field when the error ``error_var`` was caught."""
        if self.role is ErrorFieldRole.STATUS:
            return "True"
        return f"{error_var}.{self.role.value}"


def _error_read(
    term: Terminal, class_fields: list[ClusterField] | None
) -> ErrorFieldRead | None:
    """The read an Unbundle terminal makes of an error cluster field; None when
    the field is not found."""
    if term.nmux_field_index is None or not class_fields:
        return None
    flat = _flatten_fields(class_fields)
    if term.nmux_field_index >= len(flat):
        return None
    _path, field = flat[term.nmux_field_index]
    return ErrorFieldRead(
        terminal_id=term.id,
        role=error_field_role(field.type),
        no_error_value=ast.unparse(default_value_expr(field.type)),
    )


def error_unbundle_reads(
    node: PrimitiveNode, ctx: CodeGenContext
) -> list[ErrorFieldRead]:
    """The fields an Unbundle of an error cluster takes out that something
    reads (a wired output)."""
    agg = [t for t in node.terminals if t.direction == "input" and t.nmux_role == "agg"]
    if not agg or agg[0].lv_type is None or ctx.graph is None:
        return []
    class_fields = ctx.graph.get_type_fields(agg[0].lv_type, caller_vi_key=ctx.vi_name)
    reads = [
        _error_read(t, class_fields)
        for t in sorted(node.terminals, key=lambda t: t.index)
        if t.direction == "output" and t.nmux_role == "list" and ctx.is_wired(t.id)
    ]
    return [r for r in reads if r is not None]


def _position_path(
    term: Terminal, fields: list[ClusterField] | None
) -> tuple[int, ...]:
    """Map a depth-first nMux field index, including parents, to tuple positions.

    Descendants of named types are not positional; keep their indices in the
    traversal but reject a selected path through that representation boundary.
    """
    paths: list[tuple[int, ...] | None] = []

    def visit(
        current: list[ClusterField], prefix: tuple[int, ...], positional: bool
    ) -> None:
        for index, field in enumerate(current):
            path = prefix + (index,)
            paths.append(path if positional else None)
            if field.type and field.type.fields:
                visit(
                    field.type.fields,
                    path,
                    positional and _is_anonymous_cluster(field.type),
                )

    visit(fields or [], (), True)
    index = term.nmux_field_index
    if index is not None and 0 <= index < len(paths):
        path = paths[index]
        if path is not None:
            return path
    raise TypeResolutionNeeded(type_name=f"positional field[{index}]", context=term.id)


def _generate_error_bundle(
    list_in: list[Terminal],
    class_fields: list[ClusterField] | None,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate what a Bundle into an error cluster does.

    A Bundle that sets the status raises ``LabVIEWError`` with the code and
    source it bundles, when the status is true. Inside an error handler, a
    Bundle that sets only the code or the source changes the error being
    handled, which then stays raised.
    """
    values: dict[ErrorFieldRole, str] = {}
    for t in sorted(list_in, key=lambda t: t.index):
        val = ctx.resolve(t.id)
        read = _error_read(t, class_fields)
        if val and read is not None:
            values[read.role] = val

    status = values.get(ErrorFieldRole.STATUS)
    if status is None:
        if ctx.error_var is None:
            return CodeFragment.empty()
        return CodeFragment(
            statements=[
                build_assign_attr(ctx.error_var, role.value, parse_expr(val))
                for role, val in values.items()
            ]
        )

    ctx.import_labview_error()
    raise_stmt = ast.Raise(
        exc=ast.Call(
            func=ast.Name(id="LabVIEWError", ctx=ast.Load()),
            args=[],
            keywords=[
                ast.keyword(arg=role.value, value=parse_expr(values[role]))
                for role in (ErrorFieldRole.CODE, ErrorFieldRole.SOURCE)
                if role in values
            ],
        ),
        cause=None,
    )
    if status == "True":
        return CodeFragment(statements=[raise_stmt])
    return CodeFragment(
        statements=[ast.If(test=parse_expr(status), body=[raise_stmt], orelse=[])]
    )


def _field_name(
    term: Terminal,
    class_fields: list[ClusterField] | None,
) -> str | None:
    """Get Python field name for a LIST terminal using nmux_field_index, which
    counts the depth-first ``_flatten_fields`` slots."""
    if term.nmux_field_index is None or not class_fields:
        return None
    flat = _flatten_fields(class_fields)
    if term.nmux_field_index < len(flat):
        path, _field = flat[term.nmux_field_index]
        return to_var_name(path[-1])
    return None


def _field_expr(
    term: Terminal,
    agg_var: str,
    class_fields: list[ClusterField] | None,
) -> str:
    """Resolve field expression for a LIST output terminal (unbundle)."""
    if term.nmux_field_index is not None and class_fields:
        flat = _flatten_fields(class_fields)
        if term.nmux_field_index < len(flat):
            path, _field = flat[term.nmux_field_index]
            dotted = ".".join(to_var_name(p) for p in path)
            return f"{agg_var}.{dotted}"

    raise TypeResolutionNeeded(
        type_name=f"field[{term.nmux_field_index}]",
        context=term.id,
    )
