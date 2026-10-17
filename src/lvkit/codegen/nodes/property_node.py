"""Code generator for Property Nodes (propNode).

Property nodes read/write attributes on LabVIEW objects (VI Server refs,
class instances, hardware sessions). Generates Python attribute access.
"""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .base import resolve_ref_input


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
            attr=to_var_name(seg_name) if seg_name else "unknown_prop",
            ctx=ctx if i == last else ast.Load(),
        )
    return expr


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """Generate code for a property node.

    Produces attribute access:
      Read:  value = ref.property_name
      Write: ref.property_name = value
    A nested property is an attribute chain (``ref.label.text``).
    """
    properties = node.properties or []
    ref_var = resolve_ref_input(node, ctx)

    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}

    input_terms = [t for t in node.terminals if t.direction == "input"]
    output_terms = [t for t in node.terminals if t.direction == "output"]

    input_by_index = {t.index: t for t in input_terms}
    output_by_index = {t.index: t for t in output_terms}

    seen_outputs: set[str] = set()
    seen_inputs: set[str] = set()

    for prop in properties:
        prop_name = prop.name
        segment_names = [seg.name for seg in prop.segments] or [""]
        attr_name = (
            to_var_name("_".join(segment_names)) if prop_name else "unknown_prop"
        )

        # Generate reads
        for idx, term in output_by_index.items():
            if term.id in seen_outputs:
                continue
            if not ctx.is_wired(term.id):
                continue
            seen_outputs.add(term.id)
            var_name = to_var_name(f"{ref_var}_{attr_name}")
            stmt = ast.Assign(
                targets=[ast.Name(id=var_name, ctx=ast.Store())],
                value=_attribute_chain(ref_var, segment_names, ast.Load()),
            )
            statements.append(stmt)
            bindings[term.id] = var_name
            break

        # Generate writes
        for idx, term in input_by_index.items():
            if idx == 0:
                continue
            if term.id in seen_inputs:
                continue
            if not ctx.is_wired(term.id):
                continue
            seen_inputs.add(term.id)
            value = ctx.resolve(term.id)
            if value is None:
                continue
            stmt = ast.Assign(
                targets=[_attribute_chain(ref_var, segment_names, ast.Store())],
                value=parse_expr(value),
            )
            statements.append(stmt)
            break

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
