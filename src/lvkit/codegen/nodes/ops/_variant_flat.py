"""What Variant To Flattened String and Flattened String To Variant share: the
type a node's variant or type string has when the code is generated.

A variant is the Python value, so it carries no LabVIEW type at run time. The
type is known only from the graph: a variant made by To Variant has the type of
the wire fed to it, and a type string is a literal list of numbers (a diagram
constant) or the type string output of a Variant To Flattened String whose
variant has such a type."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, Terminal
from lvkit.type_string import TypeStringError, decode

from ...context import CodeGenContext
from ._flat_node import terminal

TO_VARIANT = 8201
VARIANT_TO_FLATTENED_STRING = 8203
_TYPE_STRING_OUTPUT = 1
_VARIANT_INPUT = 0
_TO_VARIANT_INPUT = 1


def _source_node(
    ctx: CodeGenContext, terminal_id: str
) -> tuple[PrimitiveNode, int] | None:
    """The primitive that feeds ``terminal_id``, and the index of the terminal on
    it the wire leaves."""
    source = ctx.get_source(terminal_id)
    if source is None or ctx.graph is None:
        return None
    found = ctx.graph.get_graph_node(source.src_parent_id)
    if not isinstance(found, PrimitiveNode):
        return None
    leaving = next((t for t in found.terminals if t.id == source.src_terminal), None)
    return (found, leaving.index) if leaving else None


def variant_static_type(ctx: CodeGenContext, node: PrimitiveNode) -> LVType | None:
    """The LabVIEW type of the variant wired to a Variant To Flattened String, or
    None when it is not known when the code is generated."""
    variant = terminal(node, "input", _VARIANT_INPUT)
    if variant is None:
        return None
    origin = _source_node(ctx, variant.id)
    if origin is None or origin[0].prim_id != TO_VARIANT:
        return None
    anything = terminal(origin[0], "input", _TO_VARIANT_INPUT)
    lv_type = anything.lv_type if anything else None
    if lv_type is None or lv_type.underlying_type == "LVVariant":
        return None
    return lv_type


def type_string_type(
    ctx: CodeGenContext, node: PrimitiveNode, type_string: Terminal | None
) -> LVType | None:
    """The type a Flattened String To Variant's ``type string`` names when the
    code is generated, or None when only a running program knows it. Raises
    ``TypeStringError`` for a literal that is not a type string."""
    if type_string is None or not ctx.is_wired(type_string.id):
        return None
    text = ctx.wired_input(type_string.id, node)
    try:
        numbers = ast.literal_eval(text or "")
    except (ValueError, SyntaxError):
        numbers = None
    if isinstance(numbers, list) and all(isinstance(n, int) for n in numbers):
        return decode(numbers)
    origin = _source_node(ctx, type_string.id)
    if (
        origin is not None
        and origin[0].prim_id == VARIANT_TO_FLATTENED_STRING
        and origin[1] == _TYPE_STRING_OUTPUT
    ):
        return variant_static_type(ctx, origin[0])
    return None


__all__ = ["TypeStringError", "type_string_type", "variant_static_type"]
