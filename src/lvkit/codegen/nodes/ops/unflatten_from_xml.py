"""Unflatten From XML (primResID 8401).

Converts an XML string that follows the LabVIEW XML schema to the type wired to
``type`` (NI, Unflatten From XML Function). ``xml.etree.ElementTree`` reads the
string; each element is checked against the type written out when the code is
generated (``codegen/xml_values.py``) and the value is built by the shared plain
value reader (``codegen/plain_values.py``).

Failures are the XML errors of NI's General LabVIEW Error Codes: an empty string
1102, a tag not in the schema 1103, a start tag with no end tag (not well formed
XML) 1104, an unexpected tag or count 1105, a tag that does not match the wired
type 1106, value text that is illegal or out of range 1108.

Terminals (primitives entry):
  in  index 4 = error in
  in  index 5 = type
  in  index 6 = xml string
  out index 0 = error out
  out index 1 = value
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...plain_values import UnsupportedPlainType
from ...xml_values import XmlErrors, read_xml
from . import register_op_fragment
from ._flat_node import terminal

ERRORS = XmlErrors(
    empty="XMLEmptyStringError",
    tag_unknown="XMLTagNotRecognizedError",
    tag_unexpected="XMLUnexpectedTagError",
    tag_mismatch="XMLTypeMismatchError",
    value="XMLValueError",
)


@register_op_fragment("UNFLATTEN_FROM_XML")
def unflatten_from_xml(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    value_term = terminal(node, "output", 1)
    if value_term is None or not ctx.is_wired(value_term.id):
        return CodeFragment.empty()
    type_term = terminal(node, "input", 5)
    lv_type = (type_term.lv_type if type_term else None) or value_term.lv_type
    if lv_type is None or type_term is None:
        raise ConversionGap("Unflatten From XML needs a type", node)
    text_term = terminal(node, "input", 6)
    text = (ctx.wired_input(text_term.id, node) if text_term else None) or "''"
    default = ctx.make_output_var("defaults", node.id)
    variable = ctx.make_output_var("value", node.id, terminal_id=value_term.id)
    try:
        statements, imports = read_xml(
            text, lv_type, default, variable, ctx, node.id, ERRORS
        )
    except UnsupportedPlainType as error:
        raise ConversionGap(
            f"Unflatten From XML does not handle {error}: the LabVIEW XML schema "
            "text of it is not read yet",
            node,
        ) from error
    for statement in imports:
        ctx.add_import(statement)
    return CodeFragment(
        statements=[
            build_assign(
                default, parse_expr(ctx.wired_input(type_term.id, node) or "None")
            ),
            *statements,
        ],
        bindings={value_term.id: variable},
    )
