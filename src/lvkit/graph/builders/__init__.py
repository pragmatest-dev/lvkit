"""Per-node-kind graph builders (the NodeTypeHandler pattern, applied to graph
construction). Mirrors parser/node_types.py: an abstract handler + a registry
keyed by node_type, so _add_vi_to_graph dispatches instead of inlining an
885-line if/elif chain. Migrated one node kind at a time (strangler-fig).
"""

from .context import GraphBuildContext
from .operations import (
    DEFAULT_NODE_BUILD_HANDLER,
    NODE_BUILD_HANDLERS,
    SUBVI_CALL_NODE_TYPES,
    NodeBuildHandler,
)
from .refs import REF_BUILD_HANDLERS, RefBuildHandler
from .structures import STRUCTURE_BUILD_HANDLERS, StructureBuildHandler

__all__ = [
    "GraphBuildContext",
    "STRUCTURE_BUILD_HANDLERS",
    "StructureBuildHandler",
    "NODE_BUILD_HANDLERS",
    "DEFAULT_NODE_BUILD_HANDLER",
    "NodeBuildHandler",
    "SUBVI_CALL_NODE_TYPES",
    "REF_BUILD_HANDLERS",
    "RefBuildHandler",
]
