"""What a VI Server reference constant references.

A Control Reference constant (``class="ctlRefConst"``) delivers a reference, not
a value. It references the VI that holds the diagram ("This VI"), the
application ("This Application") or one front-panel control. The class of a
generic reference is decoded from its type (``LVType.server_class``); a control
is the front-panel terminal the constant's ``ddo`` resolves to. The label the
author gave the constant is neither.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeGuard

from ..parser.constants import NODE_CLASS_CTL_REF_CONST
from .models import AnyGraphNode, LocalVariableNode
from .netlist_models import ReferenceTarget, ReferenceTargetKind

if TYPE_CHECKING:
    from .core import InMemoryVIGraph


def is_reference_constant(node: AnyGraphNode) -> TypeGuard[LocalVariableNode]:
    """Whether ``node`` is a Control Reference constant that delivers a reference
    (a Local Variable shares its graph node type)."""
    return (
        isinstance(node, LocalVariableNode)
        and node.node_type == NODE_CLASS_CTL_REF_CONST
        and not node.is_write
    )


def reference_target(
    graph: InMemoryVIGraph, node: LocalVariableNode
) -> ReferenceTarget:
    """What the reference constant ``node`` references."""
    if node.control_terminal_id is not None:
        control = graph.get_terminal(node.control_terminal_id)
        if control is not None and control.name:
            return ReferenceTarget(ReferenceTargetKind.CONTROL, control.name)
        return ReferenceTarget(ReferenceTargetKind.UNKNOWN)
    server_class = next(
        (t.lv_type.server_class for t in node.terminals if t.lv_type), None
    )
    if server_class == "VI":
        return ReferenceTarget(ReferenceTargetKind.THIS_VI)
    if server_class == "Application":
        return ReferenceTarget(ReferenceTargetKind.THIS_APPLICATION)
    return ReferenceTarget(ReferenceTargetKind.UNKNOWN)


def reference_phrase(target: ReferenceTarget) -> str:
    """A reference constant in LabVIEW's words, for text that names the wire's
    source: ``This VI reference``, ``Tree control reference``."""
    if target.kind is ReferenceTargetKind.CONTROL:
        return f"{target.control} control reference"
    if target.kind is ReferenceTargetKind.UNKNOWN:
        return "reference constant"
    return f"{target.kind.value} reference"
