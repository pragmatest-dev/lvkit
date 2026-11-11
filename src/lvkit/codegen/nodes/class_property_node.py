"""Code generator for a Property Node on a LabVIEW class wire.

Each row reads or writes a property of the class through its accessor VI: the
row names the property by its path in the class's project tree
(``class_model.property_accessor_vi``). A row whose accessor is a Python
property (``class_attributes``) is ``obj.name`` / ``obj.name = value``; any other
is a call of the accessor's method with the error so far, whose result gives the
value and the error.

NI (https://docs-be.ni.com/bundle/labview-api-ref/page/functions/property-node.html):
"The node executes from top to bottom. The Property Node does not execute if an
error occurs before it executes ... If an error occurs in a property, LabVIEW
ignores the remaining properties and returns an error." So the node is skipped
on an incoming error (``error_flow.guard``), its rows run in order, the rows after
one whose accessor reported an error do not run (their values take their type's
default), and the node's error out is that error. "Ignore Errors Inside Node" is
not read from the VI, so every node stops at its first error.

A Property Node on a class that is not loaded is a gap: which accessor VI each
row calls is unknown."""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.class_property import PropertyRole
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals
from lvkit.models import PropertyDef, Terminal

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..class_attributes import accessor_pane
from ..class_model import (
    check_private_access,
    class_model,
    member_shape,
    not_a_method,
    property_accessor_vi,
)
from ..context import CodeGenContext
from ..error_flow import no_error_occurred, own_error_terminals
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..type_default import type_default
from ..value_copy import owned_input


def is_class_property_node(node: PrimitiveNode) -> bool:
    """Whether the Property Node's reference input carries a LabVIEW class."""
    ref = _reference(node)
    return ref is not None and ref.lv_type is not None and bool(ref.lv_type.classname)


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The rows of a Property Node on a class wire, top to bottom."""
    assert ctx.graph is not None
    graph = ctx.graph
    ref = _reference(node)
    assert ref is not None and ref.lv_type is not None
    class_key = graph.class_type_key(ref.lv_type)
    if class_key is None or graph.is_stub(class_key):
        recorded = graph.recorded_path(class_key) if class_key else None
        where = f" ({recorded})" if recorded else ""
        raise ConversionGap(
            f"The class '{ref.lv_type.classname}'{where} the Property Node reads "
            "or writes is not loaded, so the accessor VI of each of its properties "
            "is unknown; add the class to the project or a --search-path",
            node,
        )
    rows = correlate_property_terminals(
        node.properties or [], node.terminals, node.property_value_terminal_ids
    )
    statements: list[ast.stmt] = []
    writes = any(term is not None and term.direction == "input" for _, term in rows)
    receiver = (
        owned_input(ctx, ref, node, statements)
        if writes
        else ctx.wired_input(ref.id, node)
    )
    if receiver is None:
        raise ConversionGap("The class wire of the Property Node is not wired", node)
    if not receiver.isidentifier():
        # A new object (a class constant): the rows and the reference out all
        # work on this one object.
        obj = ctx.make_output_var("class_object", node.id)
        statements.append(build_assign(obj, parse_expr(receiver)))
        receiver = obj

    error_in, error_outs = own_error_terminals(node, ctx)
    incoming = (ctx.wired_input(error_in.id, node) if error_in else None) or "None"
    error: str | None = None  # the error so far, once a row can report one
    bindings: dict[str, str] = {}
    for prop, term in rows:
        if term is None:
            raise CodeGenError("Property value terminal is missing", node)
        row = _row(node, ctx, class_key, prop, term, receiver, error or incoming)
        bindings.update(row.bindings)
        guarded_by = error
        if row.error is not None:
            if error is None:
                error = ctx.make_output_var("error_out", node.id)
                statements.insert(0, build_assign(error, parse_expr(incoming)))
            row.statements.append(build_assign(error, parse_expr(row.error)))
        if guarded_by is None:
            statements.extend(row.statements)
        else:
            # A row after one that can report an error runs only when none did.
            for var in row.bindings.values():
                statements.append(build_assign(var, type_default(ctx, term.lv_type)))
            if row.receiver is not None:
                statements.append(build_assign(row.receiver, parse_expr(receiver)))
            statements.append(
                ast.If(
                    test=parse_expr(no_error_occurred(guarded_by)),
                    body=row.statements,
                    orelse=[],
                )
            )
        if row.receiver is not None:
            receiver = row.receiver

    for out in node.terminals:
        if out.direction == "output" and out.index == 1 and not out.is_error_cluster:
            bindings[out.id] = receiver
    if error is not None:
        for out in error_outs:
            bindings[out.id] = error
    return CodeFragment(statements=statements, bindings=bindings)


@dataclass
class _Row:
    """The statements of one row, its value output's binding, the error it
    reports (an expression; None for a row that cannot report one) and the
    object the rows after it work on, when the row hands back another."""

    statements: list[ast.stmt]
    bindings: dict[str, str]
    error: str | None = None
    receiver: str | None = None


def _row(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    class_key: str,
    prop: PropertyDef,
    term: Terminal,
    receiver: str,
    error: str,
) -> _Row:
    """One row: read (``term`` an output) or write (an input) of ``prop``."""
    assert ctx.graph is not None
    graph = ctx.graph
    if len(prop.segments) != 1:
        raise ConversionGap(f"Class property row '{prop.name}' is unresolved", node)
    path = prop.segments[0].name
    reads = term.direction == "output"
    role = PropertyRole.GETTER if reads else PropertyRole.SETTER
    vi_key = property_accessor_vi(graph, class_key, path, role)
    if vi_key is None:
        raise ConversionGap(
            "Neither the class on the Property Node's class wire nor a class it "
            f"descends from lists an accessor VI that "
            f"{'reads' if reads else 'writes'} the property '{path}'",
            node,
        )
    owner = graph.owning_class_key(vi_key)
    assert owner is not None
    value: str | None = None
    if not reads:
        if not ctx.is_wired(term.id):
            raise ConversionGap("Unwired property write default is unqualified", node)
        value = ctx.wired_input(term.id, node)
        if value is None:
            raise CodeGenError("Property write value is unresolved", node)
    access = graph.get_method_access(vi_key)
    long_name = access.accessor_field if access and access.accessor_field else path
    accessor = class_model(graph, owner).attributes.accessors.get(vi_key)
    if accessor is not None:
        check_private_access(
            graph, accessor.python_name, vi_key, owner, ctx.vi_name, "A call of"
        )
        target = ast.Attribute(
            value=parse_expr(receiver),
            attr=accessor.python_name,
            ctx=ast.Load() if reads else ast.Store(),
        )
        if not reads:
            assert value is not None
            return _Row([ast.Assign(targets=[target], value=parse_expr(value))], {})
        var = ctx.make_output_var(to_var_name(long_name), node.id, term.id)
        return _Row([build_assign(var, target)], {term.id: var})
    return _method_row(
        node, ctx, owner, vi_key, term, receiver, value, error, long_name
    )


def _method_row(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    owner: str,
    vi_key: str,
    term: Terminal,
    receiver: str,
    value: str | None,
    error: str,
    long_name: str,
) -> _Row:
    """A row whose accessor is a method: called with the error so far, its
    value (named by the property's ``long_name``) and its error read from its
    result."""
    assert ctx.graph is not None
    graph = ctx.graph
    reason = not_a_method(graph, vi_key)
    if reason is not None:
        raise ConversionGap(reason, node)
    shape = member_shape(graph, vi_key)
    assert shape is not None
    check_private_access(graph, shape.name, vi_key, owner, ctx.vi_name, "A call of")
    pane = accessor_pane(graph, owner, vi_key)
    inputs, _, outputs, _ = graph.vi_interface(vi_key)
    keywords: list[ast.keyword] = []
    if value is not None and pane.value_in in shape.parameters:
        keywords.append(
            ast.keyword(arg=shape.parameters[pane.value_in], value=parse_expr(value))
        )
    for t in inputs:
        if t.is_error_cluster and t.index in shape.parameters:
            keywords.append(
                ast.keyword(arg=shape.parameters[t.index], value=parse_expr(error))
            )
    call = ast.Call(
        func=ast.Attribute(value=parse_expr(receiver), attr=shape.name, ctx=ast.Load()),
        args=[],
        keywords=keywords,
    )
    if not shape.results:
        return _Row([ast.Expr(value=call)], {})
    result = ctx.make_output_var(f"{shape.name.lstrip('_')}_result", node.id)
    bindings: dict[str, str] = {}
    if value is None and pane.value_out in shape.result_fields:
        bindings[term.id] = f"{result}.{shape.result_fields[pane.value_out]}"
    reported = next(
        (
            f"{result}.{shape.result_fields[t.index]}"
            for t in outputs
            if t.is_error_cluster and t.index in shape.result_fields
        ),
        None,
    )
    handed_back = (
        f"{result}.{shape.result_fields[pane.class_out]}"
        if shape.returns_receiver and pane.class_out in shape.result_fields
        else None
    )
    statements: list[ast.stmt] = [build_assign(result, call)]
    for term_id, expr in list(bindings.items()):
        var = ctx.make_output_var(to_var_name(long_name), node.id, term_id)
        statements.append(build_assign(var, parse_expr(expr)))
        bindings[term_id] = var
    object_var: str | None = None
    if handed_back is not None:
        object_var = ctx.make_output_var("class_object", node.id)
        statements.append(build_assign(object_var, parse_expr(handed_back)))
    return _Row(statements, bindings, reported, object_var)


def _reference(node: PrimitiveNode) -> Terminal | None:
    """The reference input (terminal index 0) of a Property Node."""
    return next(
        (t for t in node.terminals if t.direction == "input" and t.index == 0),
        None,
    )
