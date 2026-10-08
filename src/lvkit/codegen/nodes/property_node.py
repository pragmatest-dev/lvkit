"""Generate ordered attribute accesses for explicit Property Nodes."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals

from ..ast_utils import parse_expr, to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .base import CodeGenError


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Use saved dcoList identities and order, separately from fixed ports.

    The caller supplies an object implementing the named Python attributes.
    Implicit control bindings require a separate runtime model and are rejected
    instead of fabricating reference values. Error-cluster terminals are skipped:
    errors surface as Python exceptions.
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
    ref_inputs = [
        term for term in fixed if term.direction == "input" and term.index == 0
    ]
    if len(ref_inputs) != 1:
        raise CodeGenError("Property object reference is unresolved", node)
    ref_var = ctx.resolve(ref_inputs[0].id)
    if ref_var is None:
        raise CodeGenError("Property object reference is unresolved", node)

    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    attr_names: dict[str, str] = {}
    for prop, term in correlate_property_terminals(
        properties, node.terminals, value_ids
    ):
        if term is None:
            raise CodeGenError("Property value terminal is missing", node)
        if not prop.name:
            raise CodeGenError("Property name is unresolved", node)
        attr_name = to_var_name(prop.name)
        if attr_name in attr_names and attr_names[attr_name] != prop.name:
            raise CodeGenError("Normalized property names collide", node)
        attr_names[attr_name] = prop.name
        ref_expr = parse_expr(ref_var)
        if term.direction == "output":
            # Reads execute even when their result is unwired. Repeated accesses
            # to the same property need distinct snapshots, not one shared name.
            var_name = ctx.make_output_var(f"{ref_var}_{attr_name}", term.id, term.id)
            statements.append(
                ast.Assign(
                    targets=[ast.Name(id=var_name, ctx=ast.Store())],
                    value=ast.Attribute(value=ref_expr, attr=attr_name, ctx=ast.Load()),
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
                    targets=[
                        ast.Attribute(value=ref_expr, attr=attr_name, ctx=ast.Store())
                    ],
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
    if not statements:
        statements.append(
            ast.Expr(value=ast.Constant(value="# Property Node: no properties"))
        )
    return CodeFragment(statements=statements, bindings=bindings)
