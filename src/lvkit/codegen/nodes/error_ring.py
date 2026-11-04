"""Code generator for an Error Ring XNode: its error out is the error it is
configured with (``parser/error_ring.py``).

The value follows the diagram LabVIEW stores inside the ring to implement it
(``docs/_internal/design/labview-binary-format.md``, "Error Ring"):

- with the call chain included, a call of Error Cluster From Error Code with the
  ring's code, ``is warning?`` and description as ``error message`` (formatted by
  Format Into String from the ring's inputs when it takes any), so the error is
  generated as Error Cluster From Error Code's is: the description, or NI's text
  for the code, is the message;
- with the call chain excluded, an error cluster constant whose source is
  ``<err>`` and the description;
- the code 0 is no error.

A ring whose configuration does not decode, whose description uses a format
specifier other than ``%s``, or whose inputs are not strings is a gap.
"""

from __future__ import annotations

import ast
import re

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVTypeKind
from lvkit.parser.error_ring import ERROR_RING_CLASS, ErrorRing

from ..ast_utils import build_assign, parse_expr, type_default_expr
from ..context import CodeGenContext
from ..error_codes import describe_error_code
from ..errors_module import LABVIEW_ERROR_NAME
from ..fragment import CodeFragment
from . import primitive

# A Format Into String specifier: ``%%`` or ``%`` up to its conversion letter.
_SPECIFIER = re.compile(r"%(%|[^a-zA-Z%]*[a-zA-Z])")


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """An XNode: an Error Ring's error value; any other XNode is generated as a
    primitive is."""
    if node.object_name != ERROR_RING_CLASS:
        return primitive.generate(node, ctx)
    ring = node.error_ring
    if ring is None:
        raise ConversionGap(
            "Error Ring whose configuration (its StateData) does not decode", node
        )
    var = ctx.make_output_var("error", node.id)
    value = "None" if ring.code == 0 else _error(node, ring, ctx)
    outputs = [t for t in node.terminals if t.direction == "output"]
    return CodeFragment(
        statements=[build_assign(var, parse_expr(value))],
        bindings={term.id: var for term in outputs},
    )


def _error(node: PrimitiveNode, ring: ErrorRing, ctx: CodeGenContext) -> str:
    """The ``LabVIEWError`` the ring builds."""
    ctx.import_labview_error()
    status = not ring.is_warning
    if not ring.include_call_chain:
        if ring.format_inputs:
            raise ConversionGap(
                "Error Ring with format inputs and the call chain excluded", node
            )
        source = f"<err>{ring.description}" if ring.description else ""
        return (
            f"{LABVIEW_ERROR_NAME}(code={ring.code}, source={source!r}, "
            f"status={status})"
        )
    if ring.description:
        message = _message(node, ring, ctx)
    else:
        message = repr(describe_error_code(ring.code) or "")
    return f"{LABVIEW_ERROR_NAME}(code={ring.code}, message={message}, status={status})"


def _message(node: PrimitiveNode, ring: ErrorRing, ctx: CodeGenContext) -> str:
    """The description, with the ring's inputs written into its ``%s``
    specifiers in terminal order, as Format Into String does."""
    if not ring.format_inputs:
        return repr(ring.description)
    specifiers = [m.group(0) for m in _SPECIFIER.finditer(ring.description)]
    others = sorted({s for s in specifiers if s not in ("%s", "%%")})
    if others:
        raise ConversionGap(
            f"Error Ring description uses format specifier {', '.join(others)}; "
            "only %s is converted",
            node,
        )
    inputs = sorted(
        (t for t in node.terminals if t.direction == "input"), key=lambda t: t.index
    )
    if len(inputs) != ring.format_inputs or specifiers.count("%s") != len(inputs):
        raise ConversionGap(
            f"Error Ring has {len(inputs)} inputs for {specifiers.count('%s')} "
            f"%s specifiers and {ring.format_inputs} recorded format inputs",
            node,
        )
    arguments: list[str] = []
    for term in inputs:
        lv_type = term.lv_type
        if (
            lv_type is None
            or lv_type.kind is not LVTypeKind.PRIMITIVE
            or lv_type.underlying_type != "String"
        ):
            raise ConversionGap(f"Error Ring input {term.index} is not a string", node)
        wired = ctx.wired_input(term.id, node) if ctx.is_wired(term.id) else None
        arguments.append(wired or ast.unparse(type_default_expr(lv_type)))
    return f"{ring.description!r} % ({', '.join(arguments)},)"
