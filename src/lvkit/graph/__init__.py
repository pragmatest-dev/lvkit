"""lvkit.graph — In-memory VI graph package.

Re-exports InMemoryVIGraph, connect(), and LoadMode for convenient access.
"""

from .core import InMemoryVIGraph, connect
from .load_ctl import load_ctl_by_path
from .loading import LoadMode, load_vi_by_path
from .node_kinds import EdgeRel, NodeType
from .queries import AmbiguousVIReferenceError
from .typedef import TypedefField, TypedefInfo, TypedefRef

VIGraph = InMemoryVIGraph

__all__ = [
    "AmbiguousVIReferenceError",
    "EdgeRel",
    "InMemoryVIGraph",
    "LoadMode",
    "NodeType",
    "TypedefField",
    "TypedefInfo",
    "TypedefRef",
    "VIGraph",
    "connect",
    "load_ctl_by_path",
    "load_vi_by_path",
]
