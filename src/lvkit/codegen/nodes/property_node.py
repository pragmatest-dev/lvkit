"""Code generator for Property Nodes (propNode).

Property nodes read/write attributes on LabVIEW objects (VI Server refs,
class instances, hardware sessions). Generates Python attribute access.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .base import resolve_ref_input


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a property node.

    Produces attribute access:
      Read:  value = ref.property_name
      Write: ref.property_name = value
    """
    properties = node.properties or []
    ref_var = resolve_ref_input(node, ctx)

    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}

    output_terms = [t for t in node.terminals if t.direction == "output"]

    # Each property with its OWN value terminal, top to bottom: an output
    # terminal reads the property, an input terminal writes it.
    for prop, term in correlate_property_terminals(
        properties, node.terminals, node.property_value_terminal_ids
    ):
        if term is None or not ctx.is_wired(term.id):
            continue
        attr_name = to_var_name(prop.name) if prop.name else "unknown_prop"
        ref_expr = parse_expr(ref_var)
        if term.direction == "output":
            var_name = to_var_name(f"{ref_var}_{attr_name}")
            statements.append(
                ast.Assign(
                    targets=[ast.Name(id=var_name, ctx=ast.Store())],
                    value=ast.Attribute(value=ref_expr, attr=attr_name, ctx=ast.Load()),
                )
            )
            bindings[term.id] = var_name
            continue
        value = ctx.resolve(term.id)
        if value is None:
            continue
        statements.append(
            ast.Assign(
                targets=[
                    ast.Attribute(value=ref_expr, attr=attr_name, ctx=ast.Store())
                ],
                value=parse_expr(value),
            )
        )

    # Bind remaining wired outputs
    for term in output_terms:
        if term.id not in bindings and ctx.is_wired(term.id):
            var_name = to_var_name(term.name or f"{ref_var}_prop_{term.index}")
            statements.append(build_assign(var_name, parse_expr(ref_var)))
            bindings[term.id] = var_name

    if not statements:
        comment = (
            f"# Property Node: {node.object_name or 'unknown'}"
            f" - {', '.join(p.name for p in properties)}"
        )
        statements.append(ast.Expr(value=ast.Constant(value=comment)))

    return CodeFragment(statements=statements, bindings=bindings)
