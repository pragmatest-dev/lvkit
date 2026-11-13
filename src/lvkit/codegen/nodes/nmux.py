"""Code generator for Node Multiplexer (nMux) nodes.

nMux is bundle/unbundle at structure boundaries. Field resolution
uses the <i> field index from XML + dep_graph class fields. No heuristics.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import _flatten_fields
from lvkit.lv_type_codes import is_boolean_type_name, is_string_type_name
from lvkit.measure_data import measure_data_field_name, measure_data_value_type
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    Terminal,
    TypeResolutionNeeded,
    _is_error_cluster,
)

from ..ast_utils import (
    COPY_IMPORT,
    build_assign,
    parse_expr,
    to_var_name,
)
from ..class_attributes import ClassAttributes
from ..class_model import type_attributes
from ..cluster_members import attribute_paths, is_anonymous_cluster, position_path
from ..context import CodeGenContext
from ..error_flow import ErrorFieldRole
from ..fragment import CodeFragment
from ..native_array import replaced_field
from ..type_default import type_default
from ..value_copy import own_path, owned_input


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

    for t in [*list_in, *list_out]:
        if t.nmux_field_index is not None and t.nmux_field_index < 0:
            broken = ctx.graph is not None and ctx.graph.vi_is_broken(ctx.vi_name or "")
            if broken:
                reason = (
                    f"An element of {node.name!r} selects no field of its cluster "
                    f"(the VI is saved broken and stores its field index as "
                    f"{t.nmux_field_index}), so there is no value to read or write"
                )
            else:
                reason = (
                    f"An element of {node.name!r} stores its field index as "
                    f"{t.nmux_field_index}, which lvkit does not decode yet"
                )
            raise ConversionGap(reason, node)

    bindings: dict[str, str] = {}
    statements: list[ast.stmt] = []
    imports: set[str] = set()

    # Resolve AGG input variable
    agg_var = None
    if agg_in:
        agg_var = ctx.wired_input(agg_in[0].id, node)

    # AGG passthrough: bind agg outputs to agg input value
    for t in agg_out:
        if agg_var:
            bindings[t.id] = agg_var

    # Get fields for field index lookup.
    class_fields = None
    agg_terminals = agg_in or agg_out
    agg_lv_type = agg_terminals[0].lv_type if agg_terminals else None
    anon = is_anonymous_cluster(agg_lv_type)
    measure_flavor = agg_lv_type.measure_flavor if agg_lv_type else None
    class_attrs: ClassAttributes | None = None
    if agg_terminals and agg_terminals[0].lv_type:
        if ctx.graph is not None:
            class_fields = ctx.graph.get_type_fields(agg_terminals[0].lv_type)
            class_attrs = type_attributes(
                ctx.graph,
                agg_terminals[0].lv_type,
                ctx.vi_name,
                "Bundle/Unbundle By Name",
            )

    if list_in and list_out:
        # LIST in + LIST out = passthrough at structure boundary
        sorted_in = sorted(list_in, key=lambda t: t.index)
        sorted_out = sorted(list_out, key=lambda t: t.index)
        for i, t_out in enumerate(sorted_out):
            if i < len(sorted_in):
                val = ctx.wired_input(sorted_in[i].id, node)
                if val:
                    bindings[t_out.id] = val

    elif list_out and not list_in:
        # LIST out only = unbundle (extract fields from cluster)
        for t in sorted(list_out, key=lambda t: t.index):
            if agg_lv_type is not None and _is_error_cluster(agg_lv_type):
                read = _error_read(t, class_fields)
                if read is not None:
                    bindings[t.id] = read.role.in_error(agg_var)
            elif agg_var:
                if anon:
                    path = _position_path(t, class_fields)
                    bindings[t.id] = f"({agg_var})" + "".join(
                        f"[{index}]" for index in path
                    )
                elif measure_flavor:
                    bindings[t.id] = (
                        f"{agg_var}.{_measure_component(t, measure_flavor, node)}"
                    )
                else:
                    bindings[t.id] = _field_expr(t, agg_var, class_fields, class_attrs)

    elif list_in and not list_out:
        # LIST in only = bundle
        if (
            agg_terminals
            and agg_terminals[0].lv_type
            and _is_error_cluster(agg_terminals[0].lv_type)
        ):
            return _generate_error_bundle(
                node, list_in, agg_out, agg_var, class_fields, ctx
            )

        if agg_var:
            if anon:
                updates: list[tuple[tuple[int, ...], str]] = []
                for t in sorted(list_in, key=lambda t: t.index):
                    value = ctx.wired_input(t.id, node)
                    if value is None:
                        raise TypeResolutionNeeded(
                            type_name="bundle value", context=t.id
                        )
                    updates.append((_position_path(t, class_fields), value))
                cluster_var = ctx.make_output_var(
                    "cluster", node.id, agg_out[0].id if agg_out else None
                )
                statements.append(build_assign(cluster_var, parse_expr(agg_var)))
                for path, value in updates:
                    statements.append(
                        build_assign(
                            cluster_var,
                            parse_expr(replaced_field(cluster_var, path, value)),
                        )
                    )
                for t in agg_out:
                    bindings[t.id] = cluster_var
                return CodeFragment(statements=statements, bindings=bindings)
            # Bundle By Name changes the object in place: work on a copy when
            # something else can still read the incoming value.
            owned = owned_input(ctx, agg_in[0], node, statements)
            if owned is not None and owned != agg_var:
                agg_var = owned
                for t in agg_out:
                    bindings[t.id] = owned
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
                val = ctx.wired_input(t.id, node)
                if not val:
                    continue
                path = (
                    [_measure_component(t, measure_flavor, node)]
                    if measure_flavor
                    else _field_path(t, class_fields, class_attrs)
                )
                if path:
                    if len(path) > 1:
                        imports.add(COPY_IMPORT)
                        statements.extend(own_path(agg_var, path))
                    statements.append(
                        ast.Assign(
                            targets=[_attribute_target(agg_var, path)],
                            value=parse_expr(val),
                        )
                    )
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
            elts = [parse_expr(ctx.wired_input(t.id, node) or "None") for t in ordered]
            tup = ast.Tuple(elts=elts, ctx=ast.Load())
            for t in agg_out:
                bindings[t.id] = ast.unparse(tup)
        elif agg_out and measure_flavor:
            built = _build_measure_data(node, list_in, agg_out[0], measure_flavor, ctx)
            statements.extend(built.statements)
            bindings.update(built.bindings)
            imports = built.imports
        elif agg_out:
            raise ConversionGap(
                f"A Bundle into {agg_out[0].type_label()} with no cluster wired in "
                "is not converted: only a waveform or digital data type has a "
                "built-in layout to build a new value from",
                node,
            )

    return CodeFragment(statements=statements, bindings=bindings, imports=imports)


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
    leaves from and its role."""

    terminal_id: str
    role: ErrorFieldRole


def _error_read(
    term: Terminal, class_fields: list[ClusterField] | None
) -> ErrorFieldRead | None:
    """The read an Unbundle terminal makes of an error cluster field; None when
    the field is not found."""
    if term.nmux_field_index is None or not class_fields:
        return None
    flat = _flatten_fields(class_fields)
    if not 0 <= term.nmux_field_index < len(flat):
        return None
    _path, field = flat[term.nmux_field_index]
    return ErrorFieldRead(term.id, error_field_role(field.type))


def _position_path(
    term: Terminal, fields: list[ClusterField] | None
) -> tuple[int, ...]:
    """The tuple positions of the depth-first nMux field index of ``term``."""
    return position_path(term.nmux_field_index, fields, term.id)


def _measure_component(term: Terminal, flavor: str, node: PrimitiveNode) -> str:
    """The Python attribute of the waveform or digital data component a
    Bundle/Unbundle By Name terminal selects, from the type's built-in layout."""
    name = measure_data_field_name(flavor, term.nmux_field_index)
    if name is None:
        raise ConversionGap(
            f"The element {term.nmux_field_index} of {node.name!r} is not a "
            f"component of a {flavor} that lvkit knows the name of",
            node,
        )
    return to_var_name(name)


def _build_measure_data(
    node: PrimitiveNode,
    list_in: list[Terminal],
    agg_out: Terminal,
    flavor: str,
    ctx: CodeGenContext,
) -> CodeFragment:
    """A Bundle with no cluster wired in, into a waveform or digital data type:
    a new value holding the wired components, as the ``SimpleNamespace`` a
    cluster constant is. The components and their order are the type's built-in
    layout. NI's Build Waveform page says only that an unwired waveform input
    makes a new waveform "based on the components you wire", so an unwired array
    component (``Y``) is an empty array, the default of an array, and any other
    unwired one takes the default of its type."""
    layout = measure_data_value_type(flavor)
    if layout is None or not layout.fields:
        raise ConversionGap(
            f"A Bundle into {agg_out.type_label()} with no cluster wired in has no "
            "Python equivalent: lvkit does not know the type's components",
            node,
        )
    wired: dict[str, str] = {}
    for t in list_in:
        value = ctx.wired_input(t.id, node)
        if value is not None:
            wired[_measure_component(t, flavor, node)] = value
    fields = []
    for f in layout.fields:
        if f.type is not None and _is_error_cluster(f.type):
            continue
        name = to_var_name(f.name)
        if name in wired:
            value = wired[name]
        elif f.type is not None and f.type.kind == LVTypeKind.ARRAY:
            value = "[]"
        else:
            value = ast.unparse(type_default(ctx, f.type))
        fields.append(f"{name}={value}")
    var = ctx.make_output_var("cluster", node.id, agg_out.id)
    return CodeFragment(
        statements=[
            build_assign(var, parse_expr(f"SimpleNamespace({', '.join(fields)})"))
        ],
        bindings={agg_out.id: var},
        imports={"from types import SimpleNamespace"},
    )


def _generate_error_bundle(
    node: PrimitiveNode,
    list_in: list[Terminal],
    agg_out: list[Terminal],
    base: str | None,
    class_fields: list[ClusterField] | None,
    ctx: CodeGenContext,
) -> CodeFragment:
    """Generate what a Bundle into an error cluster does: a new error value whose
    bundled fields are the wired values and whose other fields are those of the
    error on the cluster input (``base``). A cluster with no error (status False,
    code 0) is ``None``, the error wire's no-error value."""
    values: dict[ErrorFieldRole, str] = {}
    for t in sorted(list_in, key=lambda t: t.index):
        read = _error_read(t, class_fields)
        val = ctx.wired_input(t.id, node)
        if read is not None and val:
            values[read.role] = val

    def field_value(role: ErrorFieldRole) -> str:
        return values.get(role) or role.in_error(base)

    status = f"({field_value(ErrorFieldRole.STATUS)})"
    code = f"({field_value(ErrorFieldRole.CODE)})"
    source = f"({field_value(ErrorFieldRole.SOURCE)})"
    ctx.import_labview_error()
    error = f"LabVIEWError(status={status}, code={code}, source={source})"
    if status != "(True)":
        occurred = " or ".join(
            test for test in (status, f"{code} != 0") if test != "(False)"
        )
        error = f"{error} if {occurred} else None"
    var = ctx.make_output_var("error", node.id)
    return CodeFragment(
        statements=[build_assign(var, parse_expr(error))],
        bindings={t.id: var for t in agg_out},
    )


def _field_path(
    term: Terminal,
    class_fields: list[ClusterField] | None,
    class_attrs: ClassAttributes | None = None,
) -> list[str] | None:
    """The attribute path from the cluster to a LIST terminal's field, using
    nmux_field_index, which counts the depth-first ``_flatten_fields`` slots."""
    if term.nmux_field_index is None or not class_fields:
        return None
    paths = attribute_paths(class_fields, class_attrs)
    if 0 <= term.nmux_field_index < len(paths):
        return paths[term.nmux_field_index]
    return None


def _attribute_target(agg_var: str, path: list[str]) -> ast.Attribute:
    """The assignment target ``agg_var.<path>``."""
    target = parse_expr(f"{agg_var}.{'.'.join(path)}")
    assert isinstance(target, ast.Attribute)
    target.ctx = ast.Store()
    return target


def _field_expr(
    term: Terminal,
    agg_var: str,
    class_fields: list[ClusterField] | None,
    class_attrs: ClassAttributes | None = None,
) -> str:
    """Resolve field expression for a LIST output terminal (unbundle)."""
    if term.nmux_field_index is not None and class_fields:
        paths = attribute_paths(class_fields, class_attrs)
        if 0 <= term.nmux_field_index < len(paths):
            return f"{agg_var}.{'.'.join(paths[term.nmux_field_index])}"

    raise TypeResolutionNeeded(
        type_name=f"field[{term.nmux_field_index}]",
        context=term.id,
    )
