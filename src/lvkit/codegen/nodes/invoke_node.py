"""Code generator for Invoke Nodes (invokeNode).

An Invoke Node calls a method of a LabVIEW class (VI Server, Application,
ActiveX, .NET). Run VI is converted by ``run_vi`` and Ctrl Val.Set, Get and Get
All (and the deprecated Set and Get All) by ``ctrl_val``, and Reinit To Default of
a control the node is bound to by
``reinit_default``; FP.Open, FP.Close and FP.Center on the VI's own front panel
(and FP.Center on another VI's reference) by ``front_panel_window``; the Tag
methods of a VI or a library by ``tag_methods``;
every other method has no Python equivalent yet, so the node
is a gap (``gaps.py``): generation warns, and the generated code raises
``NotImplementedError`` where the node would run.
"""

from __future__ import annotations

from lvkit.control_properties import is_reinit_to_default
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.graph.vi_reference import VI_FP_CENTER_METHOD_CODE, is_on_vi_reference

from ..context import CodeGenContext
from ..fragment import CodeFragment
from . import front_panel_window, reinit_default, tag_methods
from .base import DefaultObjectError, unwired_ref_class


def generate(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment:
    """An Invoke Node with a method is a gap: the reason names the method and
    the class the diagram shows. A node with no method generates nothing."""
    method_name = node.method_name or ""
    if not method_name:
        return CodeFragment.empty()

    if node.bound_control_uid and is_reinit_to_default(node.method_code):
        return reinit_default.generate(node, ctx)

    if ctx.graph is not None and ctx.graph.acts_on_own_front_panel_window(
        node, ctx.vi_name or ""
    ):
        return front_panel_window.generate(node, ctx)

    default_class = unwired_ref_class(node, ctx)
    if default_class is not None:
        raise DefaultObjectError(
            f"Invoke Node method '{method_name}' on the default {default_class} "
            "object has no Python equivalent",
            node,
        )
    if is_on_vi_reference(node) and node.method_code == VI_FP_CENTER_METHOD_CODE:
        return front_panel_window.generate_on_reference(node, ctx)
    if tag_methods.is_tag_method(node):
        return tag_methods.generate(node, ctx)

    class_name = node.object_name or ""
    on_class = f" on a {class_name} reference" if class_name else ""
    raise ConversionGap(
        f"Invoke Node method '{method_name}'{on_class} has no Python equivalent",
        node,
    )
