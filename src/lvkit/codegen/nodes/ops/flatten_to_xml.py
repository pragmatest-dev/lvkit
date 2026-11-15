"""Flatten To XML (primResID 8400 and 8402).

Converts ``anything`` to an XML string according to the LabVIEW XML schema (NI,
Flatten To XML Function). The tree is built with ``xml.etree.ElementTree`` from
the type written out when the code is generated (``codegen/xml_values.py``, which
records the text's layout and its sources); a type with no XML written here (a
SGL or EXT number, a refnum, an enum, ...) is a gap naming it.

Terminals (primitives entry):
  in  index 6 = anything
  out index 2 = xml string
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...plain_values import UnsupportedPlainType
from ...xml_values import write_xml
from . import register_op_fragment
from ._flat_node import terminal


@register_op_fragment("FLATTEN_TO_XML")
def flatten_to_xml(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    out_term = terminal(node, "output", 2)
    if out_term is None or not ctx.is_wired(out_term.id):
        return CodeFragment.empty()
    source = terminal(node, "input", 6)
    lv_type = source.lv_type if source else None
    if source is None or lv_type is None:
        raise ConversionGap("Flatten To XML needs a wired value", node)
    text = ctx.wired_input(source.id, node)
    value = ctx.make_output_var("anything", node.id)
    variable = ctx.make_output_var("xml_string", node.id, terminal_id=out_term.id)
    try:
        statements, imports = write_xml(value, lv_type, ctx, node.id, variable)
    except UnsupportedPlainType as error:
        raise ConversionGap(
            f"Flatten To XML does not handle {error}: the LabVIEW XML schema "
            "text of it is not written yet",
            node,
        ) from error
    for statement in imports:
        ctx.add_import(statement)
    return CodeFragment(
        statements=[build_assign(value, parse_expr(text or "None")), *statements],
        bindings={out_term.id: variable},
    )
