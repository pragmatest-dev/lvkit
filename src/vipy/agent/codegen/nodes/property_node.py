"""Code generator for Property Nodes (propNode).

Property nodes read/write attributes on LabVIEW objects (VI Server refs,
class instances, hardware sessions). Generates Python attribute access.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import Operation

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class PropertyNodeCodeGen(NodeCodeGen):
    """Generate code for property node reads/writes.

    Produces attribute access:
      Read:  value = ref.property_name
      Write: ref.property_name = value
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code for a property node."""
        properties = node.properties or []

        # Resolve the reference input (first input terminal is typically the object ref)
        ref_var = self._resolve_ref_input(node, ctx)

        statements: list[ast.stmt] = []
        bindings: dict[str, str] = {}

        # Track which properties are reads vs writes based on terminal wiring
        input_terms = [t for t in node.terminals if t.direction == "input"]
        output_terms = [t for t in node.terminals if t.direction == "output"]

        # Build index → terminal mappings
        input_by_index = {t.index: t for t in input_terms}
        output_by_index = {t.index: t for t in output_terms}

        # Each property maps to one output terminal (read) or one input
        # terminal (write). Pair properties with terminals by position,
        # skipping the ref terminal at index 0.
        seen_outputs: set[str] = set()
        seen_inputs: set[str] = set()

        for prop in properties:
            prop_name = prop.name
            attr_name = to_var_name(prop_name) if prop_name else "unknown_prop"

            # Generate reads: find next unprocessed wired output
            for idx, term in output_by_index.items():
                if term.id in seen_outputs:
                    continue
                if not ctx.is_wired(term.id):
                    continue
                seen_outputs.add(term.id)
                var_name = to_var_name(f"{ref_var}_{attr_name}")
                ref_expr = parse_expr(ref_var)
                stmt = ast.Assign(
                    targets=[ast.Name(id=var_name, ctx=ast.Store())],
                    value=ast.Attribute(
                        value=ref_expr,
                        attr=attr_name,
                        ctx=ast.Load(),
                    ),
                )
                statements.append(stmt)
                bindings[term.id] = var_name
                break  # One output per property

            # Generate writes: find next unprocessed wired input
            for idx, term in input_by_index.items():
                if idx == 0:
                    continue  # Skip ref input
                if term.id in seen_inputs:
                    continue
                if not ctx.is_wired(term.id):
                    continue
                seen_inputs.add(term.id)
                value = ctx.resolve(term.id)
                if value is None:
                    continue
                ref_expr = parse_expr(ref_var)
                stmt = ast.Assign(
                    targets=[
                        ast.Attribute(
                            value=ref_expr,
                            attr=attr_name,
                            ctx=ast.Store(),
                        )
                    ],
                    value=parse_expr(value),
                )
                statements.append(stmt)
                break  # One input per property

        # Bind any remaining wired output terminals that weren't handled above
        # (e.g., reference passthrough, error out, or nodes with empty properties list)
        for term in output_terms:
            if term.id not in bindings and ctx.is_wired(term.id):
                var_name = to_var_name(term.name or f"{ref_var}_prop_{term.index}")
                statements.append(build_assign(var_name, parse_expr(ref_var)))
                bindings[term.id] = var_name

        # If no statements generated, emit a comment
        if not statements:
            comment = (
                f"# Property Node: {node.object_name or 'unknown'}"
                f" - {', '.join(p.name for p in properties)}"
            )
            statements.append(ast.Expr(value=ast.Constant(value=comment)))

        return CodeFragment(statements=statements, bindings=bindings)
