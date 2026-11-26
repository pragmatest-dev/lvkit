"""Code generator for a Property Node on a VISA resource (an ``Instr`` reference).

Each row of such a node is an attribute of the VISA session, and the property code
the row records in the VI is that attribute's VISA identifier: Serial Settings:End
Mode for Writes (short name ASRL End Out) records ``0x3FFF00B4``, TCP/IP
Settings:Computer Hostname (TCP Hostname) records ``0xBFFF0196``, Message Based
Settings:Termination Character ``0x3FFF0018``. pyvisa's ``ResourceAttribute`` is the
enumeration of those identifiers, so a row reads and writes through the session's
documented ``get_visa_attribute`` / ``set_visa_attribute`` with
``ResourceAttribute(<the row's code>)``, with no table of property names.

The values are the ones VISA defines, which LabVIEW's property pages list under
the same numbers (https://docs-be.ni.com/api/bundle/labview-api-ref/page/
properties-and-methods/visa/instr/asrl-end-out.html: "(0) None, (1) Last Bit, (2)
TermChar, (3) Break", the values of ``pyvisa.constants.SerialTermination``); a
Boolean property is VISA's 0 or 1 (``VI_FALSE`` / ``VI_TRUE``).

NI (``functions/property-node.html``): the node executes its rows top to bottom,
skips when an error comes in, and "If an error occurs in a property, LabVIEW
ignores the remaining properties and returns an error." The rows are one ``try``:
a VISA failure (``pyvisa.errors.VisaIOError``) is the node's error out with the
VISA status code as the LabVIEW error code, the rows after it do not run and their
outputs take their type's default (``native_failure.catch_failures``)."""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.op_walk import correlate_property_terminals
from lvkit.models import PropertyDef, Terminal
from lvkit.primitive_resolver import NativeError

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..context import CodeGenContext
from ..errors import CodeGenError
from ..fragment import CodeFragment
from ..native_failure import CatalogFailures, catch_failures
from ..visa_session import VISA_REFNUM
from .base import resolve_ref_input, unwired_ref_class

_CODE_MASK = 0xFFFFFFFF
_SOURCE = "Property Node"
_FAILURE = NativeError(
    exception="VisaIOError", module="pyvisa.errors", code_attribute="error_code"
)


def is_visa_property_node(node: PrimitiveNode) -> bool:
    """Whether the Property Node's reference input is a VISA resource."""
    for term in node.terminals:
        if term.index == 0 and term.direction == "input":
            return (
                term.lv_type is not None
                and term.lv_type.underlying_type == "Refnum"
                and term.lv_type.ref_type == VISA_REFNUM
            )
    return False


def _attribute(code: int) -> str:
    """``pyvisa.constants.ResourceAttribute(<VISA identifier>)`` of a row's
    property code (LabVIEW stores it as a signed 32-bit number)."""
    return f"pyvisa.constants.ResourceAttribute({code & _CODE_MASK:#010X})"


def _is_boolean(term: Terminal) -> bool:
    return term.lv_type is not None and term.lv_type.underlying_type == "Boolean"


def _row(
    node: PrimitiveNode,
    ctx: CodeGenContext,
    session: str,
    prop: PropertyDef,
    term: Terminal,
    bindings: dict[str, str],
) -> ast.stmt:
    """The statement of one row: the read of the attribute into a variable, or
    the write of the value wired to it."""
    if len(prop.segments) != 1:
        raise ConversionGap(f"VISA property row '{prop.name}' is unresolved", node)
    attribute = _attribute(prop.segments[0].code)
    if term.direction == "output":
        var = ctx.make_output_var(to_var_name(prop.segments[0].name), node.id, term.id)
        read = f"{session}.get_visa_attribute({attribute})"
        if _is_boolean(term):
            read = f"bool({read})"
        bindings[term.id] = var
        return build_assign(var, parse_expr(read))
    if not ctx.is_wired(term.id):
        raise ConversionGap("Unwired property write default is unqualified", node)
    value = ctx.wired_input(term.id, node)
    if value is None:
        raise CodeGenError("Property write value is unresolved", node)
    state = f"int({value})" if _is_boolean(term) else value
    return ast.Expr(
        value=parse_expr(f"{session}.set_visa_attribute({attribute}, {state})")
    )


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """The rows of a Property Node on a VISA resource, top to bottom."""
    if unwired_ref_class(node, ctx) is not None:
        raise ConversionGap(
            "The Property Node reads or writes the attributes of a VISA resource, "
            "and no VISA resource is wired to it",
            node,
        )
    session = resolve_ref_input(node, ctx)
    value_ids = node.property_value_terminal_ids
    if len(value_ids) != len(node.properties or []):
        raise CodeGenError("Property value terminal identities are unresolved", node)
    statements: list[ast.stmt] = []
    bindings: dict[str, str] = {}
    for prop, term in correlate_property_terminals(
        node.properties or [], node.terminals, value_ids
    ):
        if term is None:
            raise CodeGenError("Property value terminal is missing", node)
        statements.append(_row(node, ctx, session, prop, term, bindings))
    for term in node.terminals:
        if (
            term.direction == "output"
            and term.index == 1
            and term.id not in value_ids
            and not term.is_error_cluster
        ):
            bindings[term.id] = session
    ctx.require_packages(["pyvisa"])
    fragment = CodeFragment(
        statements=statements, bindings=bindings, imports={"import pyvisa"}
    )
    return catch_failures(
        node, fragment, CatalogFailures((_FAILURE,), {}, _SOURCE), ctx
    )
