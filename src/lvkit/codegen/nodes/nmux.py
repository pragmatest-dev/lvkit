"""Code generator for Node Multiplexer (nMux) nodes.

nMux is bundle/unbundle at structure boundaries. Field resolution
uses the <i> field index from XML + dep_graph class fields. No heuristics.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import Enum

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
    build_assign,
    build_assign_attr,
    default_value_expr,
    field_attribute_paths,
    parse_expr,
    to_var_name,
)
from ..class_attributes import ClassAttributes, class_field_paths
from ..class_model import type_attributes
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..native_array import replaced_field
from ..value_copy import owned_input


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
    anon = _is_anonymous_cluster(agg_lv_type)
    measure_flavor = agg_lv_type.measure_flavor if agg_lv_type else None
    class_attrs: ClassAttributes | None = None
    if agg_terminals and agg_terminals[0].lv_type:
        if ctx.graph is not None:
            class_fields = ctx.graph.get_type_fields(agg_terminals[0].lv_type)
            class_attrs = type_attributes(
                ctx.graph, agg_terminals[0].lv_type, ctx.vi_name
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
            return _generate_error_bundle(node, list_in, class_fields, ctx)

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
    if not 0 <= term.nmux_field_index < len(flat):
        return None
    _path, field = flat[term.nmux_field_index]
    return ErrorFieldRead(
        terminal_id=term.id,
        role=error_field_role(field.type),
        no_error_value=ast.unparse(default_value_expr(field.type)),
    )


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
            value = ast.unparse(default_value_expr(f.type))
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
        val = ctx.wired_input(t.id, node)
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


def _attribute_paths(
    class_fields: list[ClusterField], class_attrs: ClassAttributes | None
) -> list[list[str]]:
    """The attribute path of every field of the aggregate: the attributes the
    class names its private data fields, the field names of any other cluster."""
    if class_attrs is not None:
        return class_field_paths(class_attrs, class_fields)
    return field_attribute_paths(class_fields)


def _field_path(
    term: Terminal,
    class_fields: list[ClusterField] | None,
    class_attrs: ClassAttributes | None = None,
) -> list[str] | None:
    """The attribute path from the cluster to a LIST terminal's field, using
    nmux_field_index, which counts the depth-first ``_flatten_fields`` slots."""
    if term.nmux_field_index is None or not class_fields:
        return None
    paths = _attribute_paths(class_fields, class_attrs)
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
        paths = _attribute_paths(class_fields, class_attrs)
        if 0 <= term.nmux_field_index < len(paths):
            return f"{agg_var}.{'.'.join(paths[term.nmux_field_index])}"

    raise TypeResolutionNeeded(
        type_name=f"field[{term.nmux_field_index}]",
        context=term.id,
    )
