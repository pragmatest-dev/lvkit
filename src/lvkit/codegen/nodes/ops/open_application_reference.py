"""Open Application Reference (primResID 8012).

NI, "Open Application Reference Function" (labview-api-ref,
``functions/open-application-reference.html``): "If you specify an empty string for
machine name, it returns a reference to the local LabVIEW application in which this
function is running. If you do specify a machine name, it attempts to establish a
TCP connection with a remote VI Server on that machine on the specified port."

The local application is "This Application": Python has no object for it, so the
reference is ``None`` (the Property and Invoke Nodes that read it act on the
default Application object, ``nodes/base.unwired_ref_class``). A machine name,
which opens a connection to another LabVIEW's VI Server, is a gap.

Terminals (from the primitives entry): in 11 = machine name; out 3 = application
reference.
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ...context import CodeGenContext
from ...fragment import CodeFragment
from . import register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, literal


@register_op_fragment("OPEN_APPLICATION_REFERENCE")
def open_application_reference(
    node: PrimitiveNode, ctx: CodeGenContext
) -> CodeFragment | None:
    machine = input_expression(ctx, node, 11)
    if machine is not None and literal(machine) != "":
        raise ConversionGap(
            "Open Application Reference of a remote machine: a connection to another "
            "LabVIEW's VI Server has no Python equivalent",
            node,
        )
    reference = terminal(node, "output", 3)
    if reference is None:
        return CodeFragment.empty()
    return CodeFragment(bindings={reference.id: "None"})
