"""Code generator for Invoke Nodes (invokeNode).

An Invoke Node calls a method of a LabVIEW class (VI Server, Application,
ActiveX, .NET). Run VI is converted by ``run_vi``; every other method has no
Python equivalent yet, so the node is a gap (``gaps.py``): generation warns, and
the generated code raises ``NotImplementedError`` where the node would run.
"""

from __future__ import annotations

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode

from ..context import CodeGenContext
from ..fragment import CodeFragment
from .base import DefaultObjectError, unwired_ref_class


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """An Invoke Node with a method is a gap: the reason names the method and
    the class the diagram shows. A node with no method generates nothing."""
    method_name = node.method_name or ""
    if not method_name:
        return CodeFragment.empty()

    default_class = unwired_ref_class(node, ctx)
    if default_class is not None:
        raise DefaultObjectError(
            f"Invoke Node method '{method_name}' on the default {default_class} "
            "object has no Python equivalent",
            node,
        )
    class_name = node.object_name or ""
    on_class = f" on a {class_name} reference" if class_name else ""
    raise ConversionGap(
        f"Invoke Node method '{method_name}'{on_class} has no Python equivalent",
        node,
    )
