"""Node-specific code generators.

The get_codegen() factory lives here (not in base.py) to avoid a circular
dependency: base.py defines NodeCodeGen which all node files subclass,
so base.py cannot import them at module level. This __init__.py is the
package entry point — nothing inside the package imports from it.
"""

from vipy.graph_types import Operation

from .base import (
    CodeGenError,
    MissingDependencyError,
    NodeCodeGen,
    UnknownNodeCodeGen,
    UnknownNodeError,
)
from .case import CaseCodeGen
from .compound import ArrayBuildCodeGen, CompoundArithCodeGen
from .constant import ConstantCodeGen
from .invoke_node import InvokeNodeCodeGen
from .loop import LoopCodeGen
from .nmux import NMuxCodeGen
from .primitive import PrimitiveCodeGen
from .printf import PrintfCodeGen
from .property_node import PropertyNodeCodeGen
from .sequence import FlatSequenceCodeGen
from .subvi import SubVICodeGen


def get_codegen(node: Operation, strict: bool = False) -> NodeCodeGen:
    """Factory: return appropriate CodeGen for a node.

    Args:
        node: Operation dataclass with 'labels' indicating type
        strict: If True, raise UnknownNodeError for unsupported nodes

    Returns:
        Appropriate NodeCodeGen instance

    Raises:
        UnknownNodeError: If strict=True and node type is not recognized
    """
    labels = node.labels
    node_type = node.node_type or ""

    # --- Specific node_type checks first (override generic labels) ---

    # Structural containers
    if node.loop_type in ("whileLoop", "forLoop"):
        return LoopCodeGen()
    if node_type in ("flatSequence", "seq"):
        return FlatSequenceCodeGen()
    if node_type in ("caseStruct", "select"):
        return CaseCodeGen()

    # Specialized node types (may carry generic labels like "Primitive")
    if node_type == "printf":
        return PrintfCodeGen()
    if node_type == "nMux":
        return NMuxCodeGen()
    if node_type == "propNode":
        return PropertyNodeCodeGen()
    if node_type == "invokeNode":
        return InvokeNodeCodeGen()
    if node_type == "cpdArith":
        return CompoundArithCodeGen()
    if node_type == "aBuild":
        return ArrayBuildCodeGen()

    # --- Generic label checks (fallback) ---

    if "SubVI" in labels:
        return SubVICodeGen()
    if "Primitive" in labels:
        return PrimitiveCodeGen()
    if "Constant" in labels:
        return ConstantCodeGen()
    if "FlatSequence" in labels:
        return FlatSequenceCodeGen()
    if "CaseStructure" in labels:
        return CaseCodeGen()

    # Unknown node type
    if strict:
        node_id = node.id
        node_name = node.name or "unknown"
        raise UnknownNodeError(
            f"Unknown node type: {labels} (id={node_id}, name={node_name})",
            node=node,
        )

    # Default: placeholder generator that emits a warning comment
    return UnknownNodeCodeGen()


__all__ = [
    "CodeGenError",
    "MissingDependencyError",
    "NodeCodeGen",
    "UnknownNodeError",
    "get_codegen",
]
