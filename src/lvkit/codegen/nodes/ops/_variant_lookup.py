"""What Set, Get and Delete Variant Attribute share: a variant that is an empty
Variant given attributes is LabVIEW's lookup table, written as a Python ``dict``
decided at generation time (``InMemoryVIGraph.variant_is_empty``)."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ._flat_node import terminal
from ._regex_node import input_expression

VARIANT_IN = 7


def attribute_table(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    what: str,
    statements: list[ast.stmt],
) -> tuple[str, str]:
    """The name of the ``dict`` of attributes of the node's variant input,
    assigned in ``statements``, and the expression the variant itself is (``None``
    for an empty variant). A variant that does not start as the empty Variant is
    a gap: only an empty variant given attributes is a lookup table."""
    variant = terminal(node, "input", VARIANT_IN)
    if ctx.graph is not None and variant is not None:
        if not ctx.graph.variant_is_empty(variant.id):
            raise ConversionGap(
                f"{what} on a variant that is not empty: only an empty variant "
                "given attributes is a lookup table (a dict) in Python",
                node,
            )
    text = input_expression(ctx, node, VARIANT_IN)
    table = ctx.make_output_var("attributes", node.id)
    if text in (None, "None"):
        statements.append(build_assign(table, parse_expr("{}")))
        return table, "None"
    statements.append(build_assign(table, parse_expr(f"{text} or {{}}")))
    return table, text
