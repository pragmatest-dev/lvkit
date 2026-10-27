"""Generate ordered attribute accesses for explicit Property Nodes."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode, VINode
from lvkit.graph.op_walk import correlate_property_terminals
from lvkit.models import Terminal

from ..ast_utils import parse_expr, to_var_name
from ..context import CodeGenContext
from ..default_objects import DefaultObjectProperty, default_object_property
from ..errors import CodeGenError
from ..fragment import CodeFragment
from . import vi_reference
from .base import (
    DefaultObjectError,
    resolve_ref_input,
    unwired_ref_class,
)


def _attribute_chain(
    ref_var: str, segment_names: list[str], ctx: ast.expr_context
) -> ast.expr:
    """``ref.seg1.seg2`` -- one ``ast.Attribute`` per path segment; only the
    outermost attribute carries ``ctx`` (Store for a write)."""
    expr = parse_expr(ref_var)
    last = len(segment_names) - 1
    for i, seg_name in enumerate(segment_names):
        expr = ast.Attribute(
            value=expr,
            attr=to_var_name(seg_name),
            ctx=ctx if i == last else ast.Load(),
        )
    return expr


def _callees(ctx: CodeGenContext) -> str:
    """A tuple expression of the functions of the VIs this VI calls, in diagram
    order without repeats, and the imports that bring them in."""
    names: list[str] = []
    if ctx.graph is not None and ctx.vi_name:
        for other in ctx.graph.iter_nodes(ctx.vi_name):
            if isinstance(other, VINode) and other.callee_key is not None:
                name = ctx.import_function(other.name or "", other.callee_key)
                if name != ctx.function_name and name not in names:
                    names.append(name)
    tail = "," if len(names) == 1 else ""
    return f"({', '.join(names)}{tail})"


def _default_object_expression(
    ctx: CodeGenContext, value: DefaultObjectProperty
) -> str:
    """``value.python`` with the generating VI's function and callees written in."""
    expression = value.python.replace("{function}", ctx.function_name)
    if "{callees}" in expression:
        expression = expression.replace("{callees}", _callees(ctx))
    return expression


def _default_object_read(
    node: PrimitiveNode, class_name: str, property_name: str, term: Terminal
) -> DefaultObjectProperty:
    """The Python value of a property read on the default object of
    ``class_name``; a write, or a property with no documented Python value, stops
    code generation naming the class and the property."""
    if term.direction != "output":
        raise DefaultObjectError(
            f"Property '{property_name}' is written on the default {class_name} "
            "object, which has no Python equivalent",
            node,
        )
    value = default_object_property(class_name, property_name)
    if value is None:
        raise DefaultObjectError(
            f"Property '{property_name}' of the default {class_name} object has "
            "no Python equivalent",
            node,
        )
    return value


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Use saved dcoList identities and order, separately from fixed ports.

    The caller supplies an object implementing the named Python attributes; a
    nested property is an attribute chain (``ref.label.text``). Implicit
    control bindings require a separate runtime model and are rejected
    instead of fabricating reference values. Error-cluster terminals are
    skipped: errors surface as Python exceptions.
    """
    properties = node.properties or []
    value_ids = node.property_value_terminal_ids
    if len(value_ids) != len(properties):
        raise CodeGenError("Property value terminal identities are unresolved", node)
    if len(set(value_ids)) != len(value_ids):
        raise CodeGenError("Property value terminal identities are duplicated", node)
    if len({term.id for term in node.terminals}) != len(node.terminals):
        raise CodeGenError("Property node terminal identities are duplicated", node)
    if node.bound_control_uid:
        raise ConversionGap(
            "Implicit property reference requires a binding model", node
        )
    fixed = [term for term in node.terminals if term.id not in value_ids]
    default_class = unwired_ref_class(node, ctx)
    ref_var = "" if default_class else resolve_ref_input(node, ctx)

    statements: list[ast.stmt] = []
    imports: set[str] = set()
    bindings: dict[str, str] = {}
    attr_names: dict[str, str] = {}
    for prop, term in correlate_property_terminals(
        properties, node.terminals, value_ids
    ):
        if term is None:
            raise CodeGenError("Property value terminal is missing", node)
        segment_names = [seg.name for seg in prop.segments]
        if not segment_names or not all(segment_names):
            raise ConversionGap("Property name is unresolved", node)
        attr_name = to_var_name("_".join(segment_names))
        if attr_name in attr_names and attr_names[attr_name] != prop.name:
            raise ConversionGap("Normalized property names collide", node)
        attr_names[attr_name] = prop.name
        if default_class is not None:
            value_expr = _default_object_read(node, default_class, prop.name, term)
            var_name = ctx.make_output_var(attr_name, term.id, term.id)
            statements.append(
                ast.Assign(
                    targets=[ast.Name(id=var_name, ctx=ast.Store())],
                    value=parse_expr(_default_object_expression(ctx, value_expr)),
                )
            )
            imports.update(value_expr.imports)
            bindings[term.id] = var_name
        elif term.direction == "output":
            # Reads execute even when their result is unwired. Repeated accesses
            # to the same property need distinct snapshots, not one shared name.
            var_name = ctx.make_output_var(f"{ref_var}_{attr_name}", term.id, term.id)
            reflected = vi_reference.vi_property_read(node, prop, ref_var)
            if reflected is not None:
                imports.add("import inspect")
            statements.append(
                ast.Assign(
                    targets=[ast.Name(id=var_name, ctx=ast.Store())],
                    value=reflected
                    or _attribute_chain(ref_var, segment_names, ast.Load()),
                )
            )
            bindings[term.id] = var_name
        elif term.direction == "input":
            if not ctx.is_wired(term.id):
                raise ConversionGap(
                    "Unwired property write default is unqualified", node
                )
            value = ctx.resolve(term.id)
            if value is None:
                raise CodeGenError("Property write value is unresolved", node)
            statements.append(
                ast.Assign(
                    targets=[_attribute_chain(ref_var, segment_names, ast.Store())],
                    value=parse_expr(value),
                )
            )
        else:
            raise CodeGenError("Property value direction is unresolved", node)

    for term in fixed:
        # Error clusters become Python exceptions, so their wires carry nothing.
        if term.is_error_cluster:
            continue
        if term.direction != "output" or not ctx.is_wired(term.id):
            continue
        if default_class is not None:
            raise DefaultObjectError(
                f"Property Node reference output on the default {default_class} "
                "object has no Python equivalent",
                node,
            )
        if (
            term.index == 1
            and term.lv_type
            and term.lv_type.underlying_type == "Refnum"
        ):
            bindings[term.id] = ref_var
        else:
            raise ConversionGap("Property flow-through output is unresolved", node)
    if not statements:
        statements.append(
            ast.Expr(value=ast.Constant(value="# Property Node: no properties"))
        )
    return CodeFragment(statements=statements, bindings=bindings, imports=imports)
