"""What Set, Get and Delete Variant Attribute share: their variant input is a
``Variant`` (``variants_module``), read or changed in place."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...value_copy import owned_input
from ...variant_values import variant_input
from ...variants_module import VARIANT_NAME
from ._flat_node import terminal
from ._regex_node import string_variable

VARIANT_IN = 7


def owned_variant(
    ctx: CodeGenContext, node: PrimitiveNode, statements: list[ast.stmt]
) -> str:
    """The name of the variant the node changes: the wire's own, a copy when
    another reader can still see it, a new empty ``Variant`` when unwired."""
    term = terminal(node, "input", VARIANT_IN)
    text = owned_input(ctx, term, node, statements) if term is not None else None
    if text is None:
        ctx.import_variant()
        text = f"{VARIANT_NAME}()"
    return string_variable(ctx, node, text, statements, "variant")


def read_variant(
    ctx: CodeGenContext, node: PrimitiveNode, statements: list[ast.stmt]
) -> str:
    """The name of the variant the node reads (``Variant()`` when unwired)."""
    text = variant_input(ctx, node, terminal(node, "input", VARIANT_IN))
    return string_variable(ctx, node, text, statements, "variant")
