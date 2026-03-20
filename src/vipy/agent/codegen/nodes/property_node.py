"""Code generator for Property Nodes (propNode).

Property nodes read/write attributes on LabVIEW objects (VI Server refs,
class instances, hardware sessions). Generates Python attribute access.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from vipy.graph_types import Operation

from ..ast_utils import to_var_name
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
        if not properties:
            return CodeFragment.empty()

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

        for prop in properties:
            prop_name = prop.get("name", "")
            attr_name = to_var_name(prop_name) if prop_name else "unknown_prop"

            # Check if this property has a wired output (read) or wired input (write)
            # Property nodes can have multiple properties, each with its own terminals
            # For now, generate reads for all output terminals, writes for value inputs

            # Generate reads for output terminals
            for idx, term in output_by_index.items():
                if not ctx.is_wired(term.id):
                    continue
                # Generate: var = ref.attr
                var_name = f"{ref_var}_{attr_name}"
                stmt = ast.Assign(
                    targets=[ast.Name(id=var_name, ctx=ast.Store())],
                    value=ast.Attribute(
                        value=ast.Name(id=ref_var, ctx=ast.Load()),
                        attr=attr_name,
                        ctx=ast.Load(),
                    ),
                )
                statements.append(stmt)
                bindings[term.id] = var_name

            # Generate writes for non-ref input terminals
            for idx, term in input_by_index.items():
                if idx == 0:
                    continue  # Skip ref input
                if not ctx.is_wired(term.id):
                    continue
                value = ctx.resolve(term.id)
                if value is None:
                    continue
                # Generate: ref.attr = value
                stmt = ast.Assign(
                    targets=[
                        ast.Attribute(
                            value=ast.Name(id=ref_var, ctx=ast.Load()),
                            attr=attr_name,
                            ctx=ast.Store(),
                        )
                    ],
                    value=ast.Name(id=value, ctx=ast.Load()),
                )
                statements.append(stmt)

        # If no statements generated, emit a comment
        if not statements:
            comment = (
                f"# Property Node: {node.object_name or 'unknown'}"
                f" - {', '.join(p.get('name', '?') for p in properties)}"
            )
            statements.append(ast.Expr(value=ast.Constant(value=comment)))

        return CodeFragment(statements=statements, bindings=bindings)

    def _resolve_ref_input(self, node: Operation, ctx: CodeGenContext) -> str:
        """Resolve the object reference input (typically terminal index 0)."""
        for term in node.terminals:
            if term.direction == "input" and term.index == 0:
                resolved = ctx.resolve(term.id)
                if resolved:
                    return resolved

        # Fallback: use object_name as variable
        obj_name = node.object_name or "ref"
        return to_var_name(obj_name)
