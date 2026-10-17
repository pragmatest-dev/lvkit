"""Generate ordered attribute accesses for explicit Property Nodes."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals

from ..ast_utils import parse_expr, to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .base import CodeGenError, resolve_ref_input


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
        raise CodeGenError("Implicit property reference requires a binding model", node)
    fixed = [term for term in node.terminals if term.id not in value_ids]
    ref = resolve_ref_input(node, ctx)
    ref_var = ref.name

    statements: list[ast.stmt] = list(ref.statements)
    bindings: dict[str, str] = {}
    attr_names: dict[str, str] = {}
    for prop, term in correlate_property_terminals(
        properties, node.terminals, value_ids
    ):
        if term is None:
            raise CodeGenError("Property value terminal is missing", node)
        segment_names = [seg.name for seg in prop.segments]
        if not segment_names or not all(segment_names):
            raise CodeGenError("Property name is unresolved", node)
        attr_name = to_var_name("_".join(segment_names))
        if attr_name in attr_names and attr_names[attr_name] != prop.name:
            raise CodeGenError("Normalized property names collide", node)
        attr_names[attr_name] = prop.name
        if term.direction == "output":
            # Reads execute even when their result is unwired. Repeated accesses
            # to the same property need distinct snapshots, not one shared name.
            var_name = ctx.make_output_var(f"{ref_var}_{attr_name}", term.id, term.id)
            statements.append(
                ast.Assign(
                    targets=[ast.Name(id=var_name, ctx=ast.Store())],
                    value=_attribute_chain(ref_var, segment_names, ast.Load()),
                )
            )
            bindings[term.id] = var_name
        elif term.direction == "input":
            if not ctx.is_wired(term.id):
                raise CodeGenError(
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
        if (
            term.index == 1
            and term.lv_type
            and term.lv_type.underlying_type == "Refnum"
        ):
            bindings[term.id] = ref_var
        else:
            raise CodeGenError("Property flow-through output is unresolved", node)
    if len(statements) == len(ref.statements):
        statements.append(
            ast.Expr(value=ast.Constant(value="# Property Node: no properties"))
        )
    return CodeFragment(
        statements=statements, bindings=bindings, imports=ref.imports
    )
